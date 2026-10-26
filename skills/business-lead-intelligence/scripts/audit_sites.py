#!/usr/bin/env python3
"""Audit business websites and produce evidence-backed findings.

Usage:
  python audit_sites.py --input businesses.json --output audits.json \
      --workers 6 --timeout 15 [--limit 25] [--no-link-check]

The auditor uses plain HTTP + HTML inspection so it needs no browser or paid API.
It deliberately distinguishes "broken" from "blocked to bots" and records that
limitation instead of claiming a site is down.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from common import (
    DIRECTORY_HOSTS,
    EMAIL_RE,
    PHONE_RE,
    ROLE_WORDS,
    SOCIAL_HOSTS,
    clean_phone,
    clean_text,
    dedupe_businesses,
    domain_of,
    load_json,
    normalize_phone,
    phones_match,
    root_url,
    save_json,
    severity_points,
    url_host_matches,
    utc_now,
)

try:
    import tldextract

    _TLD = tldextract.TLDExtract(suffix_list_urls=())
except Exception:  # pragma: no cover
    _TLD = None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
SOCIAL_LABELS = {
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "tiktok.com": "TikTok",
    "twitter.com": "X/Twitter",
    "x.com": "X/Twitter",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
    "t.me": "Telegram",
}


@dataclass
class Fetch:
    url: str
    final_url: str = ""
    status: int | None = None
    elapsed: float = 0.0
    headers: dict = field(default_factory=dict)
    text: str = ""
    error: str = ""
    error_kind: str = ""
    history: list = field(default_factory=list)


def classify_site(raw: str) -> tuple[str, str, str]:
    """Return (website_type, normalized_url, note)."""
    value = clean_text(raw)
    if not value:
        return "none", "", "No website field on the listing."
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    host = domain_of(value)
    if not host:
        return "none", "", "Website value could not be parsed as a URL."
    if url_host_matches(value, SOCIAL_HOSTS):
        return "social", value, "Listing points to a social profile, not an owned website."
    if url_host_matches(value, DIRECTORY_HOSTS):
        return "directory", value, "Listing points to a directory or page builder, not an owned domain."
    return "own", value, ""


def registrable_domain(host: str) -> str:
    if not host:
        return ""
    if _TLD is not None:
        ext = _TLD(host)
        if ext.domain and ext.suffix:
            return f"{ext.domain}.{ext.suffix}"
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def same_site(url: str, base_host: str) -> bool:
    host = domain_of(url)
    return bool(host) and registrable_domain(host) == registrable_domain(base_host)


def fetch(session: requests.Session, url: str, timeout: float) -> Fetch:
    started = time.monotonic()
    result = Fetch(url=url)
    try:
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        result.status = resp.status_code
        result.final_url = resp.url
        result.elapsed = time.monotonic() - started
        result.headers = {k.lower(): v for k, v in resp.headers.items()}
        result.history = [h.status_code for h in resp.history]
        ctype = result.headers.get("content-type", "")
        if "text" in ctype or "html" in ctype or "xml" in ctype or not ctype:
            result.text = resp.text[:2_500_000]
        else:
            result.error = f"Non-HTML content type: {ctype}"
            result.error_kind = "non_html"
    except requests.exceptions.SSLError as exc:
        result.error = str(exc)
        result.error_kind = "ssl_error"
        result.elapsed = time.monotonic() - started
    except requests.exceptions.Timeout as exc:
        result.error = str(exc)
        result.error_kind = "timeout"
        result.elapsed = time.monotonic() - started
    except requests.exceptions.TooManyRedirects as exc:
        result.error = str(exc)
        result.error_kind = "redirect_loop"
        result.elapsed = time.monotonic() - started
    except requests.exceptions.ConnectionError as exc:
        text = str(exc).lower()
        result.error = str(exc)
        result.error_kind = "connection_reset" if "reset" in text else "dns_or_unreachable"
        result.elapsed = time.monotonic() - started
    except Exception as exc:  # pragma: no cover
        result.error = str(exc)
        result.error_kind = "other"
        result.elapsed = time.monotonic() - started
    return result


def head_or_get(session: requests.Session, url: str, timeout: float) -> tuple[int | None, str]:
    try:
        resp = session.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code in (403, 405, 429, 501):
            resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, stream=True)
        return resp.status_code, resp.url
    except Exception as exc:
        return None, str(exc)


def dns_check(host: str) -> dict:
    try:
        infos = socket.getaddrinfo(host, None)
        ips = sorted({i[4][0] for i in infos})
        return {"ok": True, "ips": ips[:6]}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "ips": []}


def tls_check(host: str, timeout: float = 8.0) -> dict:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as wrapped:
                cert = wrapped.getpeercert() or {}
                not_after = cert.get("notAfter")
                days_left = None
                if not_after:
                    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
                        tzinfo=timezone.utc
                    )
                    days_left = (expiry - datetime.now(timezone.utc)).days
                issuer = " / ".join(
                    "=".join(part) for part in (cert.get("issuer") or ())
                )
                return {
                    "ok": True,
                    "protocol": wrapped.version(),
                    "cipher": (wrapped.cipher() or [None])[0],
                    "expires": not_after,
                    "days_left": days_left,
                    "issuer": issuer,
                }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def parse_jsonld(soup: BeautifulSoup) -> tuple[list[str], list[dict]]:
    types: list[str] = []
    objects: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
                continue
            if not isinstance(item, dict):
                continue
            obj_type = item.get("@type")
            if isinstance(obj_type, list):
                types.extend(str(t) for t in obj_type)
            elif obj_type:
                types.append(str(obj_type))
            objects.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return sorted(set(types)), objects


def text_of(node) -> str:
    return clean_text(node.get_text(" ", strip=True)) if node else ""


def visible_words(soup: BeautifulSoup) -> int:
    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return len(re.findall(r"[A-Za-z0-9']+", clone.get_text(" ", strip=True)))


def add_finding(
    findings: list[dict],
    fid: str,
    category: str,
    severity: str,
    title: str,
    evidence: str,
    why: str,
    fix: str,
    effort: str = "medium",
    page: str = "",
) -> None:
    if any(f["id"] == fid for f in findings):
        return
    findings.append(
        {
            "id": fid,
            "category": category,
            "severity": severity,
            "title": title,
            "evidence": clean_text(evidence)[:900],
            "why": clean_text(why),
            "fix": clean_text(fix),
            "effort": effort,
            "page": page,
        }
    )


def site_contact_channels(soup: BeautifulSoup, base: str) -> dict:
    emails: list[str] = []
    phones: list[str] = []
    whatsapp: list[str] = []
    social: dict[str, str] = {}
    forms: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            emails.extend(EMAIL_RE.findall(unquote(href)))
        elif href.lower().startswith("tel:"):
            phones.append(clean_phone(href[4:]))
        elif "wa.me/" in href or "api.whatsapp.com" in href:
            whatsapp.append(href)
        else:
            host = domain_of(href)
            for social_host, label in SOCIAL_LABELS.items():
                if host == social_host or host.endswith("." + social_host):
                    social.setdefault(label, href)
                    break

    body_text = soup.get_text(" ", strip=True)
    emails.extend(EMAIL_RE.findall(unquote(body_text)))
    phones.extend(PHONE_RE.findall(body_text))

    for form in soup.find_all("form"):
        inputs = form.find_all(["input", "textarea", "select"])
        names = " ".join(
            " ".join(
                filter(
                    None,
                    [
                        i.get("name", ""),
                        i.get("id", ""),
                        i.get("placeholder", ""),
                        i.get("type", ""),
                    ],
                )
            )
            for i in inputs
        ).lower()
        forms.append(
            {
                "action": urljoin(base, form.get("action", "")),
                "method": (form.get("method") or "get").upper(),
                "inputs": len(inputs),
                "has_email": "email" in names or "mail" in names,
                "has_phone": "phone" in names or "tel" in names or "mobile" in names,
                "has_message": "message" in names or "comment" in names or "enquiry" in names,
            }
        )

    def uniq(values):
        out = []
        for value in values:
            value = clean_text(value)
            if value and value.lower() not in {v.lower() for v in out}:
                out.append(value)
        return out

    emails = [e for e in uniq(emails) if not re.search(r"\.(png|jpe?g|gif|webp|svg)$", e, re.I)]
    phone_out = []
    seen_phones = set()
    for value in phones:
        digits = normalize_phone(value)
        if 7 <= len(digits) <= 15 and digits not in seen_phones:
            seen_phones.add(digits)
            phone_out.append(clean_phone(value))
    return {
        "emails": emails[:10],
        "phones": phone_out[:10],
        "whatsapp": uniq(whatsapp)[:5],
        "social": social,
        "forms": forms[:5],
        "has_contact_form": bool(forms),
        "has_tel_link": any(a.get("href", "").lower().startswith("tel:") for a in soup.find_all("a", href=True)),
        "has_mailto": any(a.get("href", "").lower().startswith("mailto:") for a in soup.find_all("a", href=True)),
    }


def detect_tech(soup: BeautifulSoup, headers: dict, html: str) -> dict:
    blob = html.lower()
    scripts = " ".join(s.get("src", "") for s in soup.find_all("script", src=True)).lower()
    generator = ""
    gen = soup.find("meta", attrs={"name": "generator"})
    if gen:
        generator = gen.get("content", "")

    tech = {
        "server": headers.get("server", ""),
        "powered_by": headers.get("x-powered-by", ""),
        "generator": clean_text(generator),
        "cms": "",
        "framework": "",
        "analytics": [],
        "marketing": [],
    }
    checks = [
        ("WordPress", ["wp-content", "wp-includes", "wp-json"], ["wordpress"]),
        ("Shopify", ["cdn.shopify.com", "shopify"], ["shopify"]),
        ("Wix", ["wix.com", "wixstatic", "x-wix"], ["wix"]),
        ("Squarespace", ["squarespace", "static1.squarespace"], ["squarespace"]),
        ("Webflow", ["webflow", "assets.website-files.com"], ["webflow"]),
        ("GoDaddy Website Builder", ["godaddysites", "websitebuilder"], ["godaddy"]),
        ("Duda", ["duda", "dudamobile"], ["duda"]),
        ("Joomla", ["joomla", "/components/com_"], ["joomla"]),
        ("Drupal", ["drupal", "/sites/default/files"], ["drupal"]),
    ]
    haystack = f"{blob} {scripts} {tech['generator'].lower()} {tech['powered_by'].lower()}"
    for name, needles, _ in checks:
        if any(n in haystack for n in needles):
            tech["cms"] = name
            break

    if "__next_data__" in blob or "/_next/" in scripts:
        tech["framework"] = "Next.js"
    elif "react" in scripts or "data-reactroot" in blob:
        tech["framework"] = "React"
    elif "vue" in scripts or "nuxt" in blob:
        tech["framework"] = "Vue/Nuxt"
    elif "bootstrap" in haystack:
        tech["framework"] = "Bootstrap"
    elif "jquery" in haystack:
        tech["framework"] = "jQuery"

    analytics = []
    if "googletagmanager.com/gtm" in blob or "gtm-" in blob:
        analytics.append("Google Tag Manager")
    if "google-analytics.com" in blob or "gtag/js" in blob:
        analytics.append("Google Analytics/GA4")
    if "connect.facebook.net" in blob or "fbq(" in blob:
        analytics.append("Meta Pixel")
    if "hotjar" in blob:
        analytics.append("Hotjar")
    if "clarity.ms" in blob:
        analytics.append("Microsoft Clarity")
    tech["analytics"] = analytics

    marketing = []
    if "calendly" in blob:
        marketing.append("Calendly")
    if "wa.me" in blob or "whatsapp" in blob:
        marketing.append("WhatsApp")
    if "mailchimp" in blob:
        marketing.append("Mailchimp")
    if "hubspot" in blob:
        marketing.append("HubSpot")
    if "tawk.to" in blob:
        marketing.append("Tawk.to chat")
    tech["marketing"] = marketing
    return tech


def pick_pages(soup: BeautifulSoup, base: str, limit: int = 6) -> list[dict]:
    scored: dict[str, int] = {}
    labels: dict[str, str] = {}
    keywords = {
        "contact": (12, "Contact"),
        "about": (9, "About"),
        "team": (9, "Team"),
        "leadership": (9, "Leadership"),
        "our-team": (9, "Team"),
        "management": (7, "Management"),
        "staff": (6, "Staff"),
        "doctor": (6, "Doctors"),
        "services": (6, "Services"),
        "treatments": (6, "Treatments"),
        "appointment": (9, "Appointment"),
        "booking": (9, "Booking"),
        "schedule": (7, "Schedule"),
        "pricing": (5, "Pricing"),
        "testimonial": (4, "Testimonials"),
        "review": (4, "Reviews"),
        "gallery": (3, "Gallery"),
        "blog": (3, "Blog"),
        "privacy": (3, "Privacy"),
    }
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].strip())
        if not href.startswith("http") or not same_site(href, domain_of(base)):
            continue
        if any(x in href.lower() for x in (".jpg", ".png", ".pdf", ".zip", "#")):
            continue
        path = (urlparse(href).path + " " + a.get_text(" ", strip=True)).lower()
        for key, (score, label) in keywords.items():
            if key in path:
                if href not in scored or score > scored[href]:
                    scored[href] = score
                    labels[href] = label
                break
    ordered = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    return [{"url": u, "label": labels.get(u, "")} for u, _ in ordered[:limit]]


def audit_one(business: dict, timeout: float = 15.0, check_links: bool = True) -> dict:
    name = clean_text(business.get("name"))
    location = clean_text(business.get("location") or business.get("city"))
    raw_site = clean_text(business.get("website"))
    website_type, site_url, site_note = classify_site(raw_site)

    result = {
        "business_name": name,
        "category": clean_text(business.get("category")),
        "location": location,
        "listed_phone": clean_phone(business.get("phone")),
        "listed_address": clean_text(business.get("address")),
        "website": raw_site,
        "website_url": site_url,
        "website_type": website_type,
        "website_status": "",
        "website_status_detail": "",
        "http_status": None,
        "final_url": "",
        "score": 0,
        "grade": "F",
        "opportunity_score": 0,
        "priority": "",
        "findings": [],
        "strengths": [],
        "contact_channels": {},
        "tech": {},
        "pages_checked": [],
        "checks": {},
        "limitations": [],
        "verified_at": utc_now(),
    }
    findings: list[dict] = result["findings"]

    if website_type == "none":
        result.update(
            website_status="no_website",
            website_status_detail="No website is listed on Google Maps.",
            score=0,
            grade="F",
            opportunity_score=100,
            priority="critical",
        )
        add_finding(
            findings,
            "site.none",
            "Presence",
            "critical",
            "No website is listed",
            "The Google Maps listing has no website field.",
            "Customers who want to check services, credibility, prices, or book online have nowhere to go. The business is invisible in organic search.",
            "Build a fast, mobile-first 5-page site: Home, Services, About, Contact, and Booking. Add LocalBusiness schema, click-to-call, a contact form, and the Google Map embed. Then add the URL to the Google Business Profile.",
            "high",
        )
        return result

    if website_type in ("social", "directory"):
        result.update(
            website_status="not_own_website",
            website_status_detail=site_note,
            score=10,
            grade="F",
            opportunity_score=92 if website_type == "social" else 88,
            priority="high",
        )
        add_finding(
            findings,
            f"site.{website_type}_only",
            "Presence",
            "high",
            f"Only a {website_type} profile is listed, not an owned website",
            f"Google Maps website field points to {raw_site} ({site_note}).",
            "The business does not control the customer experience, cannot rank for its own service keywords, and loses visitors who do not use that platform.",
            "Create an owned domain with the core pages, then link the social/directory profile to it. Keep the social profile for updates and reviews, not as the primary website.",
            "high",
        )
        result["contact_channels"] = {
            "emails": [],
            "phones": [clean_phone(business.get("phone"))] if business.get("phone") else [],
            "whatsapp": [],
            "social": {SOCIAL_LABELS.get(domain_of(raw_site), website_type.title()): raw_site},
            "forms": [],
            "has_contact_form": False,
            "has_tel_link": False,
            "has_mailto": False,
        }
        return result

    host = domain_of(site_url)
    session = requests.Session()
    page = fetch(session, site_url, timeout)

    result["checks"]["dns"] = dns_check(host)
    result["checks"]["tls"] = tls_check(host)

    if page.error:
        mapping = {
            "ssl_error": ("down_ssl_error", "TLS/SSL handshake failed."),
            "timeout": ("down_timeout", "The server did not respond before the timeout."),
            "redirect_loop": ("down_redirect_loop", "The site redirects in a loop."),
            "dns_or_unreachable": ("down_dns_or_unreachable", "DNS or TCP connection failed."),
            "connection_reset": ("down_connection_reset", "The server reset the connection."),
            "non_html": ("up_non_html", "The URL returned a non-HTML file."),
            "other": ("down_other", "The request failed for another reason."),
        }
        status, detail = mapping.get(page.error_kind, ("down_other", "Request failed."))
        result.update(
            website_status=status,
            website_status_detail=f"{detail} {page.error}".strip(),
            score=5,
            grade="F",
            opportunity_score=95,
            priority="critical",
        )
        add_finding(
            findings,
            f"availability.{page.error_kind or 'other'}",
            "Availability",
            "critical",
            "Website is unreachable",
            f"{site_url} -> {page.error_kind}: {page.error[:300]}",
            "A customer clicking the Google Maps website link hits an error, which damages trust and loses the enquiry.",
            "Fix DNS/TLS/hosting first. Confirm the domain resolves, the certificate is valid, and the origin returns 200 over HTTPS. Add uptime monitoring and a staging check before publishing changes.",
            "high",
            site_url,
        )
        return result

    result["http_status"] = page.status
    result["final_url"] = page.final_url
    result["checks"]["http"] = {
        "status": page.status,
        "elapsed_s": round(page.elapsed, 3),
        "redirect_history": page.history,
        "final_url": page.final_url,
        "content_type": page.headers.get("content-type", ""),
        "content_length": page.headers.get("content-length", ""),
        "content_encoding": page.headers.get("content-encoding", ""),
        "cache_control": page.headers.get("cache-control", ""),
        "server": page.headers.get("server", ""),
        "hsts": page.headers.get("strict-transport-security", ""),
        "csp": page.headers.get("content-security-policy", ""),
        "x_frame_options": page.headers.get("x-frame-options", ""),
        "referrer_policy": page.headers.get("referrer-policy", ""),
        "permissions_policy": page.headers.get("permissions-policy", ""),
    }

    if page.status in (403, 429):
        result.update(
            website_status="blocked_to_bots",
            website_status_detail=(
                f"HTTP {page.status}: the site blocked the automated request. "
                "It may still work for a normal visitor; verify in a real browser."
            ),
            score=50,
            grade="D",
            opportunity_score=50,
            priority="medium",
        )
        result["limitations"].append(
            "The site returned HTTP 403/429 to this auditor. Findings below may be incomplete; verify in a normal browser before contacting the business."
        )
        add_finding(
            findings,
            "availability.blocked",
            "Availability",
            "medium",
            "Website blocked the automated check",
            f"HTTP {page.status} from {site_url}.",
            "This is often bot protection, but it can also be a misconfigured firewall or geo-block that affects real customers.",
            "Verify the site in a normal browser from the target market. If real users are blocked, relax the WAF rule or allow the country/ISP ranges. Keep bot protection for admin paths only.",
            "low",
            site_url,
        )
        result["contact_channels"] = {
            "emails": [],
            "phones": [clean_phone(business.get("phone"))] if business.get("phone") else [],
            "whatsapp": [],
            "social": {},
            "forms": [],
            "has_contact_form": False,
            "has_tel_link": False,
            "has_mailto": False,
        }
        return result

    if page.status >= 400:
        result.update(
            website_status=f"down_http_{page.status}",
            website_status_detail=f"The homepage returned HTTP {page.status}.",
            score=10,
            grade="F",
            opportunity_score=92,
            priority="critical",
        )
        add_finding(
            findings,
            "availability.http_error",
            "Availability",
            "critical",
            f"Homepage returns HTTP {page.status}",
            f"{site_url} -> HTTP {page.status} at {page.final_url}.",
            "Visitors see an error page instead of the business. Search engines may drop the site from results.",
            "Restore the homepage or add a permanent redirect to a working page. Check hosting, DNS, and the CMS after the last deployment.",
            "high",
            site_url,
        )
        return result

    result["website_status"] = "up"

    soup = BeautifulSoup(page.text, "html.parser")
    result["checks"]["page"] = {
        "title": text_of(soup.title),
        "title_length": len(text_of(soup.title)),
        "meta_description": "",
        "h1": [text_of(h) for h in soup.find_all("h1")][:8],
        "word_count": visible_words(soup),
        "html_bytes": len(page.text.encode("utf-8", errors="ignore")),
        "viewport": "",
        "lang": "",
        "canonical": "",
        "robots_meta": "",
        "favicon": "",
        "og_tags": {},
        "twitter_tags": {},
        "schema_types": [],
        "images": 0,
        "images_missing_alt": 0,
        "lazy_images": 0,
        "scripts": 0,
        "external_scripts": 0,
        "inline_scripts": 0,
        "stylesheets": 0,
        "forms": 0,
        "iframes": 0,
        "mixed_content": [],
        "copyright": "",
        "social_links": {},
    }

    meta_desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta_desc:
        result["checks"]["page"]["meta_description"] = clean_text(meta_desc.get("content", ""))

    viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    if viewport:
        result["checks"]["page"]["viewport"] = clean_text(viewport.get("content", ""))

    html_tag = soup.find("html")
    if html_tag:
        result["checks"]["page"]["lang"] = clean_text(html_tag.get("lang", ""))

    canonical = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    if canonical:
        result["checks"]["page"]["canonical"] = clean_text(canonical.get("href", ""))

    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    if robots:
        result["checks"]["page"]["robots_meta"] = clean_text(robots.get("content", ""))

    favicon = soup.find("link", attrs={"rel": re.compile("icon", re.I)})
    if favicon:
        result["checks"]["page"]["favicon"] = clean_text(favicon.get("href", ""))

    for prop in ("og:title", "og:description", "og:image", "og:type", "og:url", "og:site_name"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag:
            result["checks"]["page"]["og_tags"][prop] = clean_text(tag.get("content", ""))
    for name_attr in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        tag = soup.find("meta", attrs={"name": name_attr})
        if tag:
            result["checks"]["page"]["twitter_tags"][name_attr] = clean_text(tag.get("content", ""))

    types, objects = parse_jsonld(soup)
    result["checks"]["page"]["schema_types"] = types
    result["checks"]["schema_objects"] = objects[:20]

    images = soup.find_all("img")
    result["checks"]["page"]["images"] = len(images)
    result["checks"]["page"]["images_missing_alt"] = sum(
        1 for img in images if not clean_text(img.get("alt"))
    )
    result["checks"]["page"]["lazy_images"] = sum(
        1 for img in images if (img.get("loading") or "").lower() == "lazy"
    )

    scripts = soup.find_all("script")
    result["checks"]["page"]["scripts"] = len(scripts)
    result["checks"]["page"]["external_scripts"] = sum(1 for s in scripts if s.get("src"))
    result["checks"]["page"]["inline_scripts"] = sum(1 for s in scripts if not s.get("src"))
    result["checks"]["page"]["stylesheets"] = len(soup.find_all("link", attrs={"rel": re.compile("stylesheet", re.I)}))
    result["checks"]["page"]["forms"] = len(soup.find_all("form"))
    result["checks"]["page"]["iframes"] = len(soup.find_all("iframe"))

    if page.final_url.startswith("https://"):
        mixed = []
        for tag, attr in (("img", "src"), ("script", "src"), ("link", "href"), ("iframe", "src")):
            for node in soup.find_all(tag):
                value = node.get(attr, "")
                if isinstance(value, str) and value.startswith("http://"):
                    mixed.append(value)
        result["checks"]["page"]["mixed_content"] = mixed[:10]

    body_text = soup.get_text(" ", strip=True)
    copyright_match = re.search(r"(?:©|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", body_text, re.I)
    if copyright_match:
        result["checks"]["page"]["copyright"] = copyright_match.group(1)

    channels = site_contact_channels(soup, page.final_url)
    result["contact_channels"] = channels
    result["checks"]["page"]["social_links"] = channels["social"]
    result["tech"] = detect_tech(soup, page.headers, page.text)

    malformed_whatsapp = [
        link for link in channels["whatsapp"]
        if "phone=http" in link.lower() or link.lower().count("http") > 1
    ]
    local_format_whatsapp = [
        link for link in channels["whatsapp"]
        if re.search(r"(?:wa\.me/|phone=)0\d", link)
    ]
    if local_format_whatsapp:
        add_finding(
            findings,
            "conversion.whatsapp_local_format",
            "Conversion",
            "high",
            "WhatsApp number uses a local format",
            local_format_whatsapp[0][:300],
            "A leading 0 (or a local number without a country code) makes the chat link fail for international visitors and often for the business's own country too.",
            "Use the full international number with no plus sign and no leading zero, e.g. https://wa.me/923338037161?text=Hello.",
            "low",
            page.final_url,
        )

    if malformed_whatsapp:
        add_finding(
            findings,
            "conversion.whatsapp_malformed",
            "Conversion",
            "high",
            "WhatsApp link is malformed",
            malformed_whatsapp[0][:300],
            "The button looks present but opens an invalid chat, so the visitor cannot send the message.",
            "Use the canonical form https://wa.me/<countrycode><number>?text=<url-encoded-message> with digits only, no spaces, plus signs, or nested URLs.",
            "low",
            page.final_url,
        )

    root = root_url(page.final_url)
    robots_url = urljoin(root, "/robots.txt")
    robots_resp = fetch(session, robots_url, min(timeout, 10))
    result["checks"]["robots"] = {
        "url": robots_url,
        "status": robots_resp.status if robots_resp else None,
        "text": (robots_resp.text[:4000] if robots_resp and robots_resp.text else ""),
    }
    if not robots_resp or (robots_resp.status or 0) >= 400:
        add_finding(
            findings,
            "seo.robots_missing",
            "SEO",
            "low",
            "robots.txt is missing",
            f"{robots_url} returned {robots_resp.status if robots_resp else 'no response' }.",
            "Search engines have no crawl guidance; a missing file is not fatal, but it removes control over crawl and sitemap discovery.",
            "Add /robots.txt with a Sitemap: line and only the disallow rules you actually need. Never block CSS/JS required to render the page.",
            "low",
            page.final_url,
        )
    else:
        robots_text = (robots_resp.text or "").lower()
        if re.search(r"^\s*disallow:\s*/\s*$", robots_text, re.M):
            add_finding(
                findings,
                "seo.robots_blocking",
                "SEO",
                "critical",
                "robots.txt blocks the whole site",
                "Found 'Disallow: /' in robots.txt.",
                "Google cannot crawl any page, so the site can disappear from search.",
                "Remove the global Disallow or scope it to admin/cart paths. Confirm with Search Console robots.txt tester.",
                "low",
                robots_url,
            )

    sitemap_url = urljoin(root, "/sitemap.xml")
    sitemap_resp = fetch(session, sitemap_url, min(timeout, 10))
    sitemap_ok = bool(
        sitemap_resp
        and (sitemap_resp.status or 0) < 400
        and ("<urlset" in sitemap_resp.text.lower() or "<sitemapindex" in sitemap_resp.text.lower())
    )
    result["checks"]["sitemap"] = {
        "url": sitemap_url,
        "status": sitemap_resp.status if sitemap_resp else None,
        "ok": sitemap_ok,
    }
    if not sitemap_ok:
        add_finding(
            findings,
            "seo.sitemap_missing",
            "SEO",
            "medium",
            "XML sitemap is missing or invalid",
            f"{sitemap_url} returned {sitemap_resp.status if sitemap_resp else 'no response'}; no <urlset> or <sitemapindex> found.",
            "Search engines discover new and updated pages more slowly without a sitemap.",
            "Generate /sitemap.xml with all indexable pages, reference it in robots.txt, and submit it in Google Search Console.",
            "low",
            page.final_url,
        )

    # ---------- availability / security ----------
    if page.final_url.startswith("http://"):
        add_finding(
            findings,
            "security.no_https",
            "Security",
            "critical",
            "No HTTPS",
            f"The final URL is {page.final_url}.",
            "Browsers mark the site 'Not secure', which suppresses form fills and damages trust. Google also uses HTTPS as a ranking signal.",
            "Install a TLS certificate (Let's Encrypt is free), force HTTPS with a 301 redirect, and update all internal links and assets to https://.",
            "medium",
            page.final_url,
        )

    tls = result["checks"].get("tls", {})
    if tls.get("ok") and tls.get("days_left") is not None:
        if tls["days_left"] < 0:
            add_finding(
                findings,
                "security.cert_expired",
                "Security",
                "critical",
                "TLS certificate has expired",
                f"Certificate expired {abs(tls['days_left'])} days ago.",
                "Visitors see a full-page browser warning and usually leave.",
                "Renew the certificate and enable auto-renewal. Verify the chain after renewal.",
                "low",
                site_url,
            )
        elif tls["days_left"] < 21:
            add_finding(
                findings,
                "security.cert_expiring",
                "Security",
                "high",
                "TLS certificate expires soon",
                f"Certificate expires in {tls['days_left']} days ({tls.get('expires', '')}).",
                "An expired certificate will make the site unusable and trigger browser warnings.",
                "Renew now and enable auto-renewal with monitoring at 30/14/7 days.",
                "low",
                site_url,
            )

    if result["checks"]["page"]["mixed_content"]:
        add_finding(
            findings,
            "security.mixed_content",
            "Security",
            "high",
            "HTTPS page loads insecure HTTP resources",
            f"{len(result['checks']['page']['mixed_content'])} http:// asset(s), e.g. {result['checks']['page']['mixed_content'][0]}",
            "Browsers may block the asset or show a 'not fully secure' warning. It also weakens the trust signal.",
            "Change every asset URL to https:// or a protocol-relative/CDN URL, then re-test the browser console for mixed-content warnings.",
            "low",
            page.final_url,
        )

    if page.final_url.startswith("https://") and not result["checks"]["http"].get("hsts"):
        add_finding(
            findings,
            "security.no_hsts",
            "Security",
            "low",
            "HSTS header is missing",
            "No Strict-Transport-Security response header.",
            "Without HSTS, the first request can still be downgraded to HTTP.",
            "Add: Strict-Transport-Security: max-age=31536000; includeSubDomains (start with a short max-age while testing).",
            "low",
            site_url,
        )

    if not result["checks"]["http"].get("csp"):
        add_finding(
            findings,
            "security.no_csp",
            "Security",
            "low",
            "Content-Security-Policy is missing",
            "No Content-Security-Policy response header.",
            "CSP reduces the impact of injected scripts and third-party tag abuse.",
            "Add a report-only CSP first, fix violations, then enforce it. At minimum restrict script-src to self plus required vendors.",
            "medium",
            site_url,
        )

    if not result["checks"]["http"].get("x_frame_options"):
        add_finding(
            findings,
            "security.no_frame_protection",
            "Security",
            "low",
            "Clickjacking protection header is missing",
            "No X-Frame-Options or frame-ancestors directive.",
            "The site can be embedded in another page and used for clickjacking.",
            "Add X-Frame-Options: SAMEORIGIN or CSP frame-ancestors 'self'.",
            "low",
            site_url,
        )

    server = result["checks"]["http"].get("server", "")
    powered = result["checks"]["http"].get("content_type", "")
    if re.search(r"\d+\.\d+", server):
        add_finding(
            findings,
            "security.version_disclosure",
            "Security",
            "low",
            "Server version is exposed",
            f"Server header: {server}",
            "Version disclosure helps attackers target known vulnerabilities.",
            "Remove or obfuscate the Server and X-Powered-By headers at the reverse proxy.",
            "low",
            site_url,
        )

    # ---------- SEO ----------
    p = result["checks"]["page"]
    title = p["title"]
    if not title:
        add_finding(
            findings,
            "seo.title_missing",
            "SEO",
            "high",
            "Page title is missing",
            "No <title> element was found.",
            "The title is the clickable headline in search results and the browser tab. Without it, Google invents weak text.",
            "Write a unique 50-60 character title: primary service + location + brand. Example: 'Dental Implants in Lahore | Smile Clinic'.",
            "low",
            page.final_url,
        )
    elif len(title) < 15 or len(title) > 65:
        add_finding(
            findings,
            "seo.title_length",
            "SEO",
            "medium",
            "Title length is outside the useful range",
            f"{len(title)} characters: {title}",
            "Titles shorter than ~15 characters waste ranking space; titles over ~65 are truncated in Google results.",
            "Rewrite to 50-60 characters with the main service, city, and brand name.",
            "low",
            page.final_url,
        )

    desc = p["meta_description"]
    if not desc:
        add_finding(
            findings,
            "seo.meta_description_missing",
            "SEO",
            "medium",
            "Meta description is missing",
            "No meta description was found.",
            "Google may write its own snippet, which often omits the offer and call to action.",
            "Write a 140-160 character description with the service, location, proof point, and a reason to click.",
            "low",
            page.final_url,
        )
    elif len(desc) < 70 or len(desc) > 175:
        add_finding(
            findings,
            "seo.meta_description_length",
            "SEO",
            "low",
            "Meta description length is outside the useful range",
            f"{len(desc)} characters: {desc}",
            "Short descriptions waste the snippet; long ones are truncated.",
            "Rewrite to 140-160 characters with the main keyword and a clear benefit.",
            "low",
            page.final_url,
        )

    h1s = p["h1"]
    if not h1s:
        add_finding(
            findings,
            "seo.h1_missing",
            "SEO",
            "high",
            "H1 heading is missing",
            "No <h1> was found on the homepage.",
            "The H1 tells visitors and search engines what the page is about; missing it weakens topical relevance and accessibility.",
            "Add one descriptive H1 containing the primary service and city. Keep it visually prominent.",
            "low",
            page.final_url,
        )
    elif len(h1s) > 1:
        add_finding(
            findings,
            "seo.h1_multiple",
            "SEO",
            "medium",
            "Multiple H1 headings",
            f"{len(h1s)} H1s: {' | '.join(h1s[:4])}",
            "Multiple H1s dilute the page's main topic and often signal a template/section misuse.",
            "Keep one H1 for the page topic; convert the others to H2/H3.",
            "low",
            page.final_url,
        )

    if not p["canonical"]:
        add_finding(
            findings,
            "seo.canonical_missing",
            "SEO",
            "low",
            "Canonical URL is missing",
            "No <link rel=\"canonical\"> was found.",
            "Duplicate URLs (www/non-www, tracking parameters) can split ranking signals.",
            "Add a self-referencing canonical on every indexable page.",
            "low",
            page.final_url,
        )

    if "noindex" in p["robots_meta"].lower() or "noindex" in page.headers.get("x-robots-tag", "").lower():
        add_finding(
            findings,
            "seo.noindex",
            "SEO",
            "critical",
            "Homepage is set to noindex",
            f"Robots meta: {p['robots_meta'] or page.headers.get('x-robots-tag', '')}",
            "The homepage is explicitly excluded from search results.",
            "Remove noindex from the production homepage and confirm with Search Console URL Inspection.",
            "low",
            page.final_url,
        )

    if not p["viewport"]:
        add_finding(
            findings,
            "mobile.no_viewport",
            "Mobile",
            "critical",
            "Mobile viewport meta tag is missing",
            "No viewport meta tag was found.",
            "Phones render the desktop layout zoomed out, making text and buttons hard to use. Mobile-first indexing penalises this.",
            "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> and test at 360px, 390px, and 768px widths.",
            "low",
            page.final_url,
        )
    elif "width=device-width" not in p["viewport"].lower():
        add_finding(
            findings,
            "mobile.viewport_fixed",
            "Mobile",
            "high",
            "Viewport is not responsive",
            f"Viewport meta: {p['viewport']}",
            "A fixed-width viewport forces horizontal scrolling on phones.",
            "Use width=device-width, initial-scale=1 and replace fixed pixel widths with responsive layout rules.",
            "medium",
            page.final_url,
        )

    if not p["lang"]:
        add_finding(
            findings,
            "seo.lang_missing",
            "SEO",
            "low",
            "HTML language is not declared",
            "The <html> tag has no lang attribute.",
            "Search engines and screen readers cannot reliably determine the page language.",
            "Add lang=\"en\" (or the correct primary language) to the <html> tag.",
            "low",
            page.final_url,
        )

    if p["images"] >= 8:
        missing = p["images_missing_alt"]
        ratio = missing / max(p["images"], 1)
        if ratio > 0.3:
            add_finding(
                findings,
                "accessibility.image_alt",
                "Accessibility",
                "medium",
                f"{missing} of {p['images']} images have no alt text",
                f"Alt coverage is {round((1 - ratio) * 100)}%.",
                "Missing alt text hurts screen-reader users and image search visibility.",
                "Add descriptive alt text to meaningful images and alt=\"\" to decorative images.",
                "low",
                page.final_url,
            )

    if not p["og_tags"].get("og:title") or not p["og_tags"].get("og:image"):
        add_finding(
            findings,
            "seo.open_graph",
            "SEO",
            "low",
            "Social sharing tags are incomplete",
            f"Open Graph tags found: {', '.join(p['og_tags']) or 'none'}",
            "Shared links on WhatsApp, Facebook, and LinkedIn show a bare URL instead of a rich preview.",
            "Add og:title, og:description, og:image (1200x630), og:url, and twitter:card=summary_large_image.",
            "low",
            page.final_url,
        )

    if not p["favicon"]:
        add_finding(
            findings,
            "brand.favicon_missing",
            "Brand",
            "low",
            "Favicon is missing",
            "No favicon link was found.",
            "The browser tab looks unfinished and is harder to recognise among many tabs.",
            "Add a 32x32 favicon and a 180x180 apple-touch-icon.",
            "low",
            page.final_url,
        )

    business_name = name.lower()
    if title and business_name and business_name not in title.lower():
        add_finding(
            findings,
            "seo.brand_in_title",
            "SEO",
            "low",
            "Business name is missing from the title",
            f"Title: {title}",
            "Brand terms help returning customers and reduce confusion when multiple similar businesses rank.",
            "Include the brand name at the end of the title if it fits within 60 characters.",
            "low",
            page.final_url,
        )

    if location and location.lower() not in f"{title} {desc}".lower():
        add_finding(
            findings,
            "seo.location_missing",
            "SEO",
            "medium",
            "Target location is missing from the title and description",
            f"Location '{location}' was not found in the title or meta description.",
            "Local search results rely heavily on clear location relevance. The page may rank for the service but not in the target city.",
            f"Add '{location}' naturally to the title, H1, meta description, and contact/address block. Create a dedicated location page if the business serves multiple areas.",
            "low",
            page.final_url,
        )

    if p["word_count"] < 300:
        add_finding(
            findings,
            "content.thin",
            "Content",
            "medium",
            "Homepage content is thin",
            f"About {p['word_count']} visible words on the homepage.",
            "Thin pages give search engines and buyers too little information to judge relevance, expertise, or services.",
            "Add service descriptions, who you help, proof (reviews, credentials), pricing/insurance guidance, and FAQs. Aim for 500-900 useful words, not filler.",
            "medium",
            page.final_url,
        )

    schema_types = p["schema_types"]
    has_local = any(t in {"LocalBusiness", "Dentist", "MedicalClinic", "Physician", "Store", "Restaurant", "ProfessionalService", "HealthAndBeautyBusiness"} for t in schema_types)
    if not schema_types:
        add_finding(
            findings,
            "seo.schema_missing",
            "SEO",
            "medium",
            "Structured data is missing",
            "No JSON-LD schema was found.",
            "Search engines cannot reliably read the business name, address, phone, hours, and services for rich results.",
            "Add LocalBusiness (or the most specific subtype) JSON-LD with name, url, telephone, address, geo, openingHoursSpecification, image, and sameAs.",
            "medium",
            page.final_url,
        )
    elif not has_local:
        add_finding(
            findings,
            "seo.schema_local_business",
            "SEO",
            "medium",
            "No LocalBusiness schema",
            f"Schema types found: {', '.join(schema_types[:8])}",
            "Generic Organization/WebSite markup does not give search engines the local business details needed for local rich results.",
            "Add LocalBusiness or the most specific subtype with name, address, phone, hours, geo, and sameAs.",
            "medium",
            page.final_url,
        )
    else:
        local_objs = [
            o for o in result["checks"].get("schema_objects", [])
            if str(o.get("@type", "")).split("/")[-1] in {
                "LocalBusiness", "Dentist", "MedicalClinic", "Physician", "Store",
                "Restaurant", "ProfessionalService", "HealthAndBeautyBusiness",
            }
        ]
        if local_objs:
            obj = local_objs[0]
            missing_fields = [
                label for key, label in (
                    ("telephone", "telephone"),
                    ("address", "address"),
                    ("openingHoursSpecification", "opening hours"),
                    ("geo", "geo coordinates"),
                    ("image", "image"),
                ) if not obj.get(key)
            ]
            if missing_fields:
                add_finding(
                    findings,
                    "seo.schema_incomplete",
                    "SEO",
                    "low",
                    "LocalBusiness schema is incomplete",
                    f"Missing: {', '.join(missing_fields)}",
                    "Incomplete markup is less likely to qualify for rich local results.",
                    "Add the missing properties, validate with Google's Rich Results Test, and keep them consistent with the Google Business Profile.",
                    "low",
                    page.final_url,
                )

    # ---------- contact / trust / conversion ----------
    if not channels["has_tel_link"]:
        add_finding(
            findings,
            "conversion.no_tel_link",
            "Conversion",
            "high",
            "Phone number is not click-to-call",
            "No tel: link was found on the homepage.",
            "On mobile, visitors must copy the number manually, which loses calls.",
            "Wrap the phone number in <a href=\"tel:+92...\"> and repeat it in the header and contact section.",
            "low",
            page.final_url,
        )

    if not channels["has_contact_form"] and not channels["emails"]:
        add_finding(
            findings,
            "conversion.no_contact_method",
            "Conversion",
            "high",
            "No contact form or email address on the homepage",
            "No <form> and no mailto/email address were found.",
            "A visitor who is not ready to call has no low-friction way to enquire.",
            "Add a short form (name, phone/email, service, message) with spam protection and a clear response-time promise.",
            "medium",
            page.final_url,
        )
    elif channels["has_contact_form"]:
        form = channels["forms"][0]
        if not form.get("has_phone") and not form.get("has_email"):
            add_finding(
                findings,
                "conversion.form_fields",
                "Conversion",
                "medium",
                "Contact form has no email or phone field",
                f"Form inputs: {form.get('inputs')} with action {form.get('action')}",
                "The business cannot reply to the enquiry.",
                "Add at least one reply channel (phone or email), make it required, and confirm the submission is delivered to a monitored inbox.",
                "low",
                page.final_url,
            )
        if form.get("action", "").startswith("http://"):
            add_finding(
                findings,
                "security.form_insecure",
                "Security",
                "critical",
                "Contact form submits over HTTP",
                f"Form action: {form.get('action')}",
                "Enquiry data, including phone and email, can be intercepted or flagged by the browser.",
                "Post the form to an HTTPS endpoint and redirect the page to HTTPS.",
                "low",
                page.final_url,
            )

    if not channels["whatsapp"] and not any("whatsapp" in v.lower() for v in channels["social"].values()):
        add_finding(
            findings,
            "conversion.no_whatsapp",
            "Conversion",
            "low",
            "No WhatsApp contact option",
            "No wa.me / WhatsApp link was found.",
            "In many local markets WhatsApp is the preferred low-friction channel, especially for mobile visitors.",
            "Add a floating WhatsApp button with a pre-filled message and track clicks as a conversion.",
            "low",
            page.final_url,
        )

    body_lower = body_text.lower()
    if not any(word in body_lower for word in ("book", "appointment", "schedule", "consultation", "enquire", "contact us", "get a quote", "call now")):
        add_finding(
            findings,
            "conversion.no_cta",
            "Conversion",
            "medium",
            "No clear call to action",
            "No booking/appointment/quote/contact CTA text was found on the homepage.",
            "Visitors may understand the service but not know what to do next.",
            "Add one primary CTA above the fold (Book appointment / Get a quote / Call now) and repeat it after the proof section.",
            "low",
            page.final_url,
        )

    if not any(word in body_lower for word in ("review", "testimonial", "patient story", "client story", "google review", "rated")):
        add_finding(
            findings,
            "trust.no_social_proof",
            "Trust",
            "medium",
            "No reviews or testimonials on the homepage",
            "No review/testimonial language was found.",
            "Buyers look for evidence from other customers before contacting a local business.",
            "Add 3-6 short reviews with names/photos where permitted, plus a link to the Google Business Profile reviews.",
            "low",
            page.final_url,
        )

    if not any(word in body_lower for word in ("address", "location", "visit us", "find us", "directions")):
        add_finding(
            findings,
            "trust.no_address",
            "Trust",
            "medium",
            "No address or location block on the homepage",
            "No address/location/visit-us language was found.",
            "Local buyers need to confirm the business is nearby and legitimate.",
            "Add the full address, a Google Map embed, parking/access notes, and opening hours in the footer and contact section.",
            "low",
            page.final_url,
        )

    if not any(word in body_lower for word in ("monday", "tuesday", "opening hours", "open hours", "mon-fri", "hours")):
        add_finding(
            findings,
            "trust.no_hours",
            "Trust",
            "low",
            "Opening hours are missing",
            "No opening-hours text was found on the homepage.",
            "Visitors cannot tell whether the business is open now or when to call.",
            "Add opening hours and mark them up with openingHoursSpecification. Keep them identical to Google Maps.",
            "low",
            page.final_url,
        )

    if not any(word in body_lower for word in ("privacy", "terms", "cookie")):
        add_finding(
            findings,
            "trust.no_privacy",
            "Trust",
            "medium",
            "Privacy policy is missing",
            "No privacy/terms/cookie link text was found.",
            "A form collecting phone/email without a privacy notice is a legal and trust risk in most markets.",
            "Add a plain-language privacy policy covering what is collected, why, retention, and how to request deletion. Link it near every form.",
            "low",
            page.final_url,
        )

    if p["copyright"]:
        try:
            year = int(p["copyright"])
            current_year = datetime.now().year
            if year < current_year - 1:
                add_finding(
                    findings,
                    "trust.stale_copyright",
                    "Trust",
                    "low",
                    f"Copyright year is {year}",
                    f"The footer still says {year}.",
                    "An old copyright date makes the business look inactive or abandoned.",
                    f"Update the footer to {current_year} or make it dynamic.",
                    "low",
                    page.final_url,
                )
        except ValueError:
            pass

    # ---------- performance ----------
    if page.elapsed > 3.0:
        add_finding(
            findings,
            "performance.slow_ttfb",
            "Performance",
            "high",
            "Homepage response is slow",
            f"Initial HTML response took {page.elapsed:.1f}s (TTFB plus download).",
            "Slow responses increase mobile bounce rates and hurt Core Web Vitals.",
            "Enable server-side caching/CDN, compress HTML, and move heavy work off the critical path. Target <1s TTFB from the target market.",
            "medium",
            page.final_url,
        )
    elif page.elapsed > 1.5:
        add_finding(
            findings,
            "performance.ttfb",
            "Performance",
            "medium",
            "Homepage response could be faster",
            f"Initial HTML response took {page.elapsed:.1f}s.",
            "Every extra second of load time reduces conversions, especially on mobile networks.",
            "Add a CDN and page cache, then measure again from the target country.",
            "medium",
            page.final_url,
        )

    html_kb = len(page.text.encode("utf-8", errors="ignore")) / 1024
    if html_kb > 500:
        add_finding(
            findings,
            "performance.html_size",
            "Performance",
            "medium",
            "HTML document is very large",
            f"{html_kb:.0f} KB of HTML.",
            "Large documents take longer to parse on low-end phones and often indicate inline bloat.",
            "Move inline CSS/JS to cached files, remove unused builders/blocks, and lazy-load below-the-fold content.",
            "medium",
            page.final_url,
        )

    if p["external_scripts"] + p["stylesheets"] > 25:
        add_finding(
            findings,
            "performance.requests",
            "Performance",
            "medium",
            "Many render-blocking scripts and stylesheets",
            f"{p['external_scripts']} external scripts and {p['stylesheets']} stylesheets.",
            "Each request adds latency and CPU work before the page becomes usable.",
            "Bundle and minify assets, defer non-critical JS, remove unused plugins/tags, and self-host critical fonts.",
            "medium",
            page.final_url,
        )

    if not result["checks"]["http"].get("content_encoding"):
        add_finding(
            findings,
            "performance.no_compression",
            "Performance",
            "medium",
            "Text compression is not enabled",
            "No Content-Encoding header on the HTML response.",
            "Uncompressed HTML/CSS/JS can be 3-5x larger than necessary.",
            "Enable gzip or Brotli at the server/CDN for text/html, CSS, JS, JSON, and SVG.",
            "low",
            page.final_url,
        )

    if p["images"] > 8 and p["lazy_images"] == 0:
        add_finding(
            findings,
            "performance.no_lazy_loading",
            "Performance",
            "low",
            "Below-the-fold images are not lazy-loaded",
            f"{p['images']} images and 0 with loading=\"lazy\".",
            "All images compete for bandwidth on the first load, delaying the main content.",
            "Add loading=\"lazy\" to below-the-fold images and keep the hero image eager with explicit width/height.",
            "low",
            page.final_url,
        )

    if not result["checks"]["http"].get("cache_control"):
        add_finding(
            findings,
            "performance.no_cache_headers",
            "Performance",
            "low",
            "No cache-control header",
            "The HTML response has no Cache-Control header.",
            "Repeat visitors re-download assets and HTML unnecessarily.",
            "Set a short cache for HTML (e.g. 5-15 min) and long immutable caching for hashed assets.",
            "low",
            page.final_url,
        )

    # ---------- links / pages ----------
    pages = pick_pages(soup, page.final_url, limit=6)
    for item in pages:
        sub = fetch(session, item["url"], timeout)
        page_info = {
            "label": item["label"],
            "url": item["url"],
            "status": sub.status,
            "final_url": sub.final_url,
            "error": sub.error_kind,
            "title": "",
            "h1": "",
            "words": 0,
            "has_form": False,
        }
        if not sub.error and sub.text:
            sub_soup = BeautifulSoup(sub.text, "html.parser")
            page_info["title"] = text_of(sub_soup.title)
            page_info["h1"] = text_of(sub_soup.find("h1"))
            page_info["words"] = visible_words(sub_soup)
            page_info["has_form"] = bool(sub_soup.find("form"))
        result["pages_checked"].append(page_info)

    contact_pages = [x for x in result["pages_checked"] if x["label"] == "Contact"]
    if not contact_pages:
        add_finding(
            findings,
            "conversion.no_contact_page",
            "Conversion",
            "medium",
            "No dedicated contact page",
            "No internal link to a contact page was found.",
            "Buyers expect a stable contact page with address, phone, hours, and a form they can find from the menu.",
            "Create /contact with the full NAP, click-to-call, form, map, hours, and parking/access notes. Link it in the header and footer.",
            "low",
            page.final_url,
        )

    if check_links:
        candidates: list[str] = []
        for a in soup.find_all("a", href=True):
            href = urljoin(page.final_url, a["href"].strip())
            if href.startswith("http") and not any(
                x in href.lower() for x in (".jpg", ".png", ".pdf", ".zip", "mailto:", "tel:", "javascript:")
            ):
                if href not in candidates:
                    candidates.append(href)
            if len(candidates) >= 18:
                break
        broken = []
        unverified = []
        for link in candidates:
            status, final = head_or_get(session, link, min(timeout, 10))
            if status is None:
                unverified.append({"url": link, "error": final[:160]})
            elif status in (404, 410):
                broken.append({"url": link, "status": status})
            elif status >= 500:
                broken.append({"url": link, "status": status})
            elif status in (401, 403, 429):
                unverified.append({"url": link, "status": status})
        result["checks"]["links"] = {
            "checked": len(candidates),
            "broken": broken[:10],
            "unverified": unverified[:10],
        }
        if broken:
            add_finding(
                findings,
                "seo.broken_links",
                "SEO",
                "high",
                f"{len(broken)} broken link(s) found",
                "; ".join(f"{b['url']} -> HTTP {b['status']}" for b in broken[:5]),
                "Broken links waste crawl budget, frustrate visitors, and can point to removed services or old contact details.",
                "Fix or redirect each broken URL with a 301 to the closest live page. Re-crawl after the fix.",
                "low",
                page.final_url,
            )

    # ---------- NAP consistency ----------
    listed_phone = normalize_phone(business.get("phone", ""))
    if listed_phone:
        site_phones = [normalize_phone(x) for x in channels["phones"]]
        if site_phones and not any(phones_match(business.get("phone", ""), p) for p in channels["phones"]):
            add_finding(
                findings,
                "trust.phone_mismatch",
                "Trust",
                "high",
                "Phone number differs from Google Maps",
                f"Google Maps: {business.get('phone')}; website: {', '.join(channels['phones'][:3])}",
                "Inconsistent NAP data confuses customers and weakens local search trust.",
                "Use one primary number everywhere (Google Business Profile, website, social, directories). If tracking numbers are needed, keep the primary number visible and use call tracking only for ads.",
                "low",
                page.final_url,
            )

    listed_address = clean_text(business.get("address")).lower()
    if listed_address and len(listed_address) > 10:
        body_lower = body_text.lower()
        # Compare the street/number tokens rather than the full formatted address.
        tokens = [t for t in re.split(r"[\s,]+", listed_address) if len(t) > 3][:8]
        matched = sum(1 for t in tokens if t in body_lower)
        if tokens and matched / len(tokens) < 0.35:
            add_finding(
                findings,
                "trust.address_mismatch",
                "Trust",
                "medium",
                "Address on the website does not clearly match Google Maps",
                f"Google Maps: {business.get('address')}; no close match found in the homepage text.",
                "Mismatched address data weakens local trust and can send customers to the wrong place.",
                "Add the exact Google Maps address to the footer and contact page. Keep suite/floor/landmark formatting identical.",
                "low",
                page.final_url,
            )

    # ---------- strengths ----------
    strengths = []
    if page.final_url.startswith("https://"):
        strengths.append("HTTPS is enabled")
    if p["viewport"]:
        strengths.append("Responsive viewport is declared")
    if title:
        strengths.append(f"Title present ({len(title)} chars)")
    if desc:
        strengths.append("Meta description present")
    if h1s:
        strengths.append("H1 present")
    if has_local:
        strengths.append("LocalBusiness structured data present")
    if channels["has_contact_form"]:
        strengths.append("Contact form present")
    if channels["has_tel_link"]:
        strengths.append("Click-to-call phone link present")
    if channels["whatsapp"]:
        strengths.append("WhatsApp contact present")
    if p["images"] and p["images_missing_alt"] / max(p["images"], 1) <= 0.3:
        strengths.append("Most images have alt text")
    if page.elapsed <= 1.5:
        strengths.append(f"Fast initial response ({page.elapsed:.2f}s)")
    links_check = result["checks"].get("links")
    if links_check is not None and not links_check.get("broken"):
        strengths.append("No broken links in the sampled set")
    if len(pages) >= 3:
        strengths.append(f"{len(pages)} key internal pages discovered")
    result["strengths"] = strengths

    # ---------- scoring ----------
    deduction = sum(severity_points(f["severity"]) for f in findings)
    score = max(0, min(100, 100 - deduction))
    result["score"] = score
    result["grade"] = grade_for(score)
    result["opportunity_score"] = max(0, min(100, 100 - score))
    result["priority"] = priority_for(result)
    result["limitations"].append(
        "Automated checks cover HTTP, HTML, SEO, mobile meta, trust/conversion signals, and sampled links. "
        "They do not measure real-user Core Web Vitals, visual design quality, or JavaScript-rendered content. "
        "Use a browser/Lighthouse pass before promising performance numbers."
    )
    return result


def grade_for(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 65:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def priority_for(result: dict) -> str:
    ids = {f["id"] for f in result["findings"]}
    critical_ids = {
        "site.none", "availability.http_error", "availability.ssl_error",
        "availability.timeout", "availability.dns_or_unreachable",
        "security.no_https", "security.cert_expired", "seo.noindex",
        "mobile.no_viewport", "security.form_insecure",
    }
    if ids & critical_ids or result["score"] < 35:
        return "critical"
    if result["score"] < 55 or result["opportunity_score"] >= 70:
        return "high"
    if result["score"] < 75:
        return "medium"
    return "low"


def audit_businesses(
    businesses: list[dict],
    workers: int = 6,
    timeout: float = 15.0,
    check_links: bool = True,
    limit: int | None = None,
) -> list[dict]:
    rows = dedupe_businesses(businesses)
    if limit:
        rows = rows[:limit]
    results: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(audit_one, row, timeout, check_links): row for row in rows
        }
        for future in futures.as_completed(future_map):
            row = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:  # keep the batch alive
                results.append(
                    {
                        "business_name": clean_text(row.get("name")),
                        "website": clean_text(row.get("website")),
                        "website_type": "unknown",
                        "website_status": "audit_error",
                        "score": 0,
                        "grade": "F",
                        "opportunity_score": 100,
                        "priority": "high",
                        "findings": [
                            {
                                "id": "audit.error",
                                "category": "Audit",
                                "severity": "high",
                                "title": "Audit failed",
                                "evidence": str(exc)[:500],
                                "why": "The automated audit could not complete.",
                                "fix": "Re-run this business individually and verify in a browser.",
                                "effort": "low",
                                "page": "",
                            }
                        ],
                        "strengths": [],
                        "contact_channels": {},
                        "tech": {},
                        "pages_checked": [],
                        "checks": {},
                        "limitations": [str(exc)[:300]],
                        "verified_at": utc_now(),
                    }
                )
    order = {clean_text(b.get("name")).lower(): i for i, b in enumerate(rows)}
    results.sort(key=lambda r: order.get(clean_text(r.get("business_name")).lower(), 999))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit business websites.")
    parser.add_argument("--input", required=True, help="Businesses JSON (list or {businesses:[...]}).")
    parser.add_argument("--output", default="audits.json")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-link-check", action="store_true")
    args = parser.parse_args()

    payload = load_json(args.input, [])
    businesses = payload.get("businesses", []) if isinstance(payload, dict) else payload
    if not businesses:
        raise SystemExit(f"No businesses found in {args.input}")

    results = audit_businesses(
        businesses,
        workers=args.workers,
        timeout=args.timeout,
        check_links=not args.no_link_check,
        limit=args.limit,
    )
    save_json(args.output, {"generated_at": utc_now(), "audits": results})
    print(f"Audited {len(results)} businesses -> {args.output}")
    for r in results:
        print(
            f"  {r['score']:>3}/100 {r['grade']}  {r['priority']:<8} "
            f"{r['website_status']:<20} {r['business_name'][:55]}"
        )


if __name__ == "__main__":
    main()
