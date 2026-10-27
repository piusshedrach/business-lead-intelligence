#!/usr/bin/env python3
"""Merge discovery, audit, and decision-maker data into sales-ready deliverables.

Outputs:
  all_leads.csv
  website_opportunity_leads.csv
  site_findings.csv
  decision_makers.csv
  FINDINGS.md
  cold_emails.md
  run_summary.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote_plus

from common import (
    clean_text,
    dedupe_businesses,
    load_json,
    save_csv,
    save_json,
    utc_now,
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
STATUS_LABELS = {
    "no_website": "No website listed",
    "not_own_website": "Social/directory only",
    "up": "Live",
    "blocked_to_bots": "Live but blocked the audit bot",
    "down_http_4xx": "Broken (4xx)",
    "down_http_5xx": "Broken (5xx)",
    "down_ssl_error": "Broken TLS/SSL",
    "down_timeout": "Timeout",
    "down_dns_or_unreachable": "DNS/unreachable",
    "down_redirect_loop": "Redirect loop",
    "down_connection_reset": "Connection reset",
    "down_other": "Unreachable",
    "up_non_html": "Non-HTML response",
    "audit_error": "Audit error",
}


def key(name: str) -> str:
    return clean_text(name).lower()


def top_findings(findings: list[dict], limit: int = 5) -> list[dict]:
    return sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "low"), 9), f.get("category", "")),
    )[:limit]


def recommended_offer(audit: dict) -> str:
    status = audit.get("website_status", "")
    score = audit.get("score", 0)
    if status == "no_website":
        return "New 5-page website + Google Business Profile optimization + lead form"
    if status == "not_own_website":
        return "Owned website to replace the social/directory listing + booking/contact flow"
    if status.startswith("down") or status == "up_non_html":
        return "Emergency hosting/TLS repair + uptime monitoring + relaunch"
    if score < 40:
        return "Full website rebuild or rescue: performance, mobile, SEO, trust, and conversion"
    if score < 65:
        return "Conversion + local SEO overhaul: service pages, schema, reviews, forms, speed"
    if score < 80:
        return "Targeted optimization retainer: technical fixes, content, and CRO"
    return "Light-touch retainer or monitoring; site is already functional"


def qualification(audit: dict) -> str:
    if audit.get("priority") == "critical":
        return "hot"
    if audit.get("priority") == "high":
        return "warm"
    if audit.get("priority") == "medium":
        return "nurture"
    return "low_priority"


def primary_email(business: dict, audit: dict) -> str:
    email = clean_text(business.get("email"))
    if email:
        return email
    channels = audit.get("contact_channels") or {}
    emails = channels.get("emails") or []
    return clean_text(emails[0]) if emails else ""


def short_evidence(finding: dict, limit: int = 130) -> str:
    text = clean_text(finding.get("evidence"))
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;,")
    return text[:limit]


def possessive(name: str) -> str:
    name = clean_text(name)
    if not name:
        return "the business"
    if name.endswith("'"):
        return name + "s"
    if name.endswith("s"):
        return name + "'"
    return name + "'s"


def outreach_hook(business: dict, audit: dict, findings: list[dict]) -> str:
    """One evidence-backed observable to open the conversation with."""
    status = audit.get("website_status", "")
    rating = clean_text(business.get("rating"))
    reviews = clean_text(business.get("reviews"))
    proof = ""
    if rating and reviews:
        proof = f" despite {rating}★ from {reviews} reviews"
    elif rating:
        proof = f" despite a {rating}★ rating"

    if status == "no_website":
        return f"No website linked on Google Maps{proof}."
    if status == "not_own_website":
        return f"Only a social/directory listing is linked, not an owned website{proof}."
    if status.startswith("down") or status == "up_non_html":
        label = STATUS_LABELS.get(status, "Website unreachable")
        detail = ""
        critical = next((f for f in findings if f.get("severity") == "critical"), None)
        if critical:
            detail = f" — {short_evidence(critical)}"
        return f"{label}{detail}."
    top = next(
        (f for f in findings if f.get("severity") in ("critical", "high")),
        None,
    )
    if not top:
        ranked = top_findings(findings, 1)
        top = ranked[0] if ranked else None
    if top:
        return f"{top.get('title')} — {short_evidence(top)}"
    if audit.get("score") is not None:
        return f"Site is live but scores {audit.get('score')}/100; no critical issue was detected."
    return "Site is live; no critical issue was detected."


def opening_line(business: dict, audit: dict, hook: str) -> str:
    name = clean_text(business.get("name"))
    owner = possessive(name)
    status = audit.get("website_status", "")
    hook = clean_text(hook).rstrip(".")
    if status == "no_website":
        return (
            f"Hi [Name], I noticed {name} has strong Google reviews but no website linked, "
            "so customers cannot check services or book online. I build simple 5-page sites "
            "for local businesses. Want me to send a quick mockup?"
        )
    if status == "not_own_website":
        return (
            f"Hi [Name], {name} currently relies on a social/directory listing instead of an "
            "owned website. I can turn that into a proper site with booking and contact capture. "
            "Open to a quick look?"
        )
    if status.startswith("down") or status == "up_non_html":
        return (
            f"Hi [Name], {owner} website is currently unreachable ({hook}). I can restore it "
            "and set up uptime monitoring. Open to a short call?"
        )
    return (
        f"Hi [Name], I ran a quick audit of {owner} website: {hook}. "
        "Want the short list of fixes, ranked by impact?"
    )


def buyer_candidate(dm: dict) -> dict | None:
    for cand in dm.get("candidates") or []:
        if cand.get("seniority") in ("owner", "executive", "management"):
            return cand
    return None


def short_business_name(name: str) -> str:
    """A short, human label for subject lines and mockup offers."""
    value = clean_text(name).strip()
    value = re.sub(r"^the\s+", "", value, flags=re.I)
    value = value.split(" - ")[0].strip()
    value = value.rstrip("'’").strip()
    suffixes = [
        "multispeciality dental care",
        "dental works and aesthetics",
        "dental clinic",
        "dental care",
        "dental centre",
        "dental center",
        "dental hospital",
        "clinics",
        "clinic",
        "dentist",
        "dental",
        "hospital",
    ]
    low = value.lower()
    for suffix in suffixes:
        if low == suffix:
            value = ""
            break
        if low.endswith(" " + suffix):
            value = value[: -(len(suffix) + 1)].strip()
            break
    if not value:
        value = clean_text(name)
    words = value.split()
    if len(words) > 3:
        words = words[:3]
    while words and words[-1].lower() in {"and", "&", "of", "the"}:
        words.pop()
    value = " ".join(words)
    if value.isdigit():
        original_words = clean_text(name).split()
        if len(original_words) >= 2:
            value = " ".join(original_words[:2])
    return value or clean_text(name)


def email_greeting(dm: dict) -> str:
    buyer = buyer_candidate(dm)
    if not buyer:
        return "Hi team,"
    name = clean_text(buyer.get("name")).split(",")[0]
    parts = name.split()
    if not parts:
        return "Hi team,"
    if name.lower().startswith("dr"):
        return f"Hi Dr. {parts[-1]},"
    return f"Hi {parts[0]},"


EMAIL_RULE_ORDER = [
    ("no https", "https"),
    ("insecure http resources", "insecure"),
    ("contact form submits over http", "form_http"),
    ("no contact form or email", "no_contact"),
    ("homepage response is slow", "slow"),
    ("homepage response could be faster", "slow"),
    ("phone number is not click-to-call", "phone_link"),
    ("phone number differs from google maps", "phone_mismatch"),
    ("whatsapp link is malformed", "whatsapp"),
    ("whatsapp number uses a local format", "whatsapp_local"),
    ("h1 heading is missing", "h1"),
    ("multiple h1 headings", "h1"),
    ("no address or location block", "address"),
    ("no reviews or testimonials", "reviews"),
    ("privacy policy", "privacy"),
    ("broken link", "broken_link"),
    ("html document is very large", "heavy"),
    ("many render-blocking", "render"),
    ("images have no alt text", "alt"),
    ("no whatsapp contact option", "no_whatsapp"),
    ("business name is missing from the title", "title"),
]


def email_finding(findings: list[dict], score: int = 0) -> tuple[dict | None, str]:
    for pattern, kind in EMAIL_RULE_ORDER:
        for finding in findings:
            title = (finding.get("title") or "").lower()
            if pattern not in title:
                continue
            if kind == "slow":
                match = re.search(r"(\d+(?:\.\d+)?)\s*s\b", clean_text(finding.get("evidence")))
                if score >= 80:
                    continue
                if match and float(match.group(1)) < 3.0:
                    continue
            return finding, kind
    ranked = top_findings(findings, 1)
    return (ranked[0], "generic") if ranked else (None, "generic")


def email_sentence(text: str) -> str:
    text = clean_text(text).replace("—", ", ").replace("–", ", ")
    text = re.sub(r"\s+", " ", text).strip()
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return text


EMAIL_SUBJECT_SUFFIX = {
    "https": "security",
    "insecure": "security",
    "form_http": "security",
    "no_contact": "booking",
    "slow": "speed",
    "phone_link": "phone",
    "phone_mismatch": "phone",
    "whatsapp": "whatsapp",
    "whatsapp_local": "whatsapp",
    "h1": "headline",
    "address": "address",
    "reviews": "reviews",
    "privacy": "privacy",
    "broken_link": "link",
    "heavy": "speed",
    "render": "speed",
    "alt": "images",
    "no_whatsapp": "whatsapp",
    "title": "title",
    "generic": "site",
}


def email_cta(kind: str) -> str:
    offers = {
        "https": "I can send the two fixes that would secure it. Want them?",
        "insecure": "I can send the two fixes that would secure it. Want them?",
        "form_http": "I can send the one change that protects the form. Want it?",
        "no_contact": "I can send a simple contact and booking fix. Want it?",
        "slow": "I can send the two changes that would speed it up most. Want them?",
        "phone_link": "I can send the one-line fix for the phone link. Want it?",
        "phone_mismatch": "I can send the exact places to update the number. Want them?",
        "whatsapp": "I can send the corrected WhatsApp link. Want it?",
        "whatsapp_local": "I can send the corrected WhatsApp link. Want it?",
        "h1": "I can send a clearer homepage heading. Want it?",
        "address": "I can send a simple location block for the homepage. Want it?",
        "reviews": "I can send a simple review section for the homepage. Want it?",
        "privacy": "I can send a plain-language privacy policy template. Want it?",
        "broken_link": "I can send the broken link and a replacement. Want it?",
        "heavy": "I can send the two changes that would make it lighter. Want them?",
        "render": "I can send the two changes that would make it lighter. Want them?",
        "alt": "I can send the short alt-text fix list. Want it?",
        "no_whatsapp": "I can send a simple WhatsApp button you can add. Want it?",
        "title": "I can send a better page title. Want it?",
        "generic": "I can send the short fix list. Want it?",
    }
    return offers.get(kind, "I can send the short fix list. Want it?")


def email_problem(finding: dict, kind: str, audit: dict) -> tuple[str, str]:
    """Translate an audit finding into a customer-facing observation and value."""
    title = (finding.get("title") or "").lower()
    evidence = clean_text(finding.get("evidence"))
    if kind == "https":
        return (
            "The site still loads over http, so the connection is not secure.",
            "Browsers can flag an insecure connection, which is a hard first impression for a dental practice.",
        )
    if kind == "insecure":
        return (
            "The site loads some insecure content even though the page itself is https.",
            "Browsers can warn visitors about insecure content, which undercuts trust before they read anything.",
        )
    if kind == "form_http":
        return (
            "The contact form submits over http, so patient details are not protected.",
            "A form on a healthcare site should protect the details people share before they book.",
        )
    if kind == "no_contact":
        return (
            "There is no contact form or email address on the homepage.",
            "A patient who is ready to book has to leave the site and call, which adds friction at the moment they are deciding.",
        )
    if kind == "slow":
        match = re.search(r"(\d+(?:\.\d+)?)\s*s\b", evidence)
        if match:
            seconds = f"{float(match.group(1)):g}"
            observation = f"The homepage takes about {seconds} seconds to respond."
            value = (
                f"On a phone, a {seconds}-second wait is a long time before a patient "
                "sees your services."
            )
        else:
            observation = "The homepage is slow to respond."
            value = "A slow first load makes it easy for people to leave before they see your services."
        return observation, value
    if kind == "phone_link":
        return (
            "The phone number is not tappable on mobile.",
            "On a phone, patients have to copy the number instead of tapping it, which adds friction at the moment they want to call.",
        )
    if kind == "phone_mismatch":
        return (
            "The phone number on the site differs from the one on your Google listing.",
            "Patients who call the number on the site may reach a different line than the one they found on Maps.",
        )
    if kind == "whatsapp":
        return (
            "The WhatsApp link is malformed, so the button may not open a chat.",
            "A contact route you already offer should work on the first tap, especially for patients who prefer messaging.",
        )
    if kind == "whatsapp_local":
        return (
            "The WhatsApp link uses a local number format, so it may not open a chat correctly.",
            "A working WhatsApp link gives patients a low-friction way to ask a question before they book.",
        )
    if kind == "h1":
        return (
            "The homepage has no clear main heading.",
            "A clear main heading helps visitors and Google understand what you do in the first second.",
        )
    if kind == "address":
        return (
            "The homepage does not show your clinic address.",
            "Showing the address helps patients confirm the location before they call or visit.",
        )
    if kind == "reviews":
        return (
            "The site does not show any of your Google reviews.",
            "Showing even a few of those reviews on the homepage would give visitors the same trust signal they see on Google.",
        )
    if kind == "privacy":
        return (
            "There is no privacy policy on the site.",
            "A privacy policy is a basic trust signal for a healthcare site, especially when patients share contact details.",
        )
    if kind == "broken_link":
        return (
            "One link on the homepage is broken.",
            "A broken link sends visitors to a dead end and makes the site feel less maintained.",
        )
    if kind == "heavy":
        match = re.search(r"(\d+)\s*KB", evidence)
        observation = (
            f"The homepage loads {match.group(1)} KB of HTML."
            if match
            else "The homepage is heavier than it needs to be."
        )
        return (
            observation,
            "A lighter page is easier to use on a phone and usually feels faster.",
        )
    if kind == "render":
        return (
            "The page loads a lot of scripts and stylesheets before the content appears.",
            "Trimming that overhead makes the first screen appear sooner for patients on mobile.",
        )
    if kind == "alt":
        match = re.search(r"(\d+)\s+of\s+(\d+)", evidence)
        observation = (
            f"{match.group(1)} of {match.group(2)} images on the homepage have no alt text."
            if match
            else "Some images on the homepage have no alt text."
        )
        return (
            observation,
            "Alt text makes the site usable for screen-reader visitors and gives Google more context about your images.",
        )
    if kind == "no_whatsapp":
        return (
            "There is no WhatsApp option on the site.",
            "Adding WhatsApp gives patients a low-friction way to ask a question before they book.",
        )
    if kind == "title":
        return (
            "The browser title does not include the clinic name.",
            "The title is what people see in search results and tabs, so including the clinic name makes it easier to recognize.",
        )
    observation = short_evidence(finding, 150) or "There is one issue on the site worth fixing."
    if title:
        observation = f"{finding.get('title', '').rstrip('.')}: {observation}"
    return (
        email_sentence(observation),
        "Small fixes like this make the site easier to use for patients who are already looking.",
    )


def cold_email(business: dict, audit: dict, dm: dict, sender: dict) -> tuple[str, str, str, str, str]:
    """Build a short, evidence-based first-touch email and its send status."""
    name = clean_text(business.get("name"))
    email = primary_email(business, audit)
    buyer = buyer_candidate(dm)
    short = short_business_name(name)
    status = audit.get("website_status", "")
    rating = clean_text(business.get("rating"))
    reviews = clean_text(business.get("reviews"))
    score = audit.get("score", 0)
    findings = audit.get("findings") or []
    greeting = email_greeting(dm)
    proof = ""
    if rating and reviews:
        proof = f"You have {rating} stars from {reviews} Google reviews. "
    elif rating:
        proof = f"You have a {rating} star Google rating. "

    email_evidence = outreach_hook(business, audit, findings)

    if status == "no_website":
        subject = f"{short} website"
        observation = (
            f"{proof}There is no website linked from your Google listing."
            if proof
            else "There is no website linked from your Google listing."
        )
        value = (
            "A simple site with your services, dentist profiles, and a booking form "
            "would give people a clear next step after they find you on Maps."
        )
        cta = f"If useful, I can send a one-page mockup for {short}, no call needed. Worth a look?"
    elif status == "not_own_website":
        subject = f"{short} website"
        observation = (
            f"{proof}The Google listing points to a social or directory page instead of an owned website."
            if proof
            else "The Google listing points to a social or directory page instead of an owned website."
        )
        value = (
            "An owned site would give you one place for services, dentist profiles, and "
            "booking instead of sending patients to a social feed."
        )
        cta = "I can send a simple site outline and a mockup. Want me to?"
    elif status.startswith("down") or status == "up_non_html":
        subject = f"{short} site"
        if status.startswith("down_http_5"):
            problem = "is returning a server error"
        elif status.startswith("down_http_4"):
            problem = "is returning a page-not-found error"
        elif status == "down_ssl_error":
            problem = "has a security certificate problem"
        elif status == "down_timeout":
            problem = "is timing out"
        else:
            problem = "is not loading"
        observation = (
            f"{proof}The website linked from your Google listing {problem}."
            if proof
            else f"The website linked from your Google listing {problem}."
        )
        value = (
            "Getting it back up protects the trust your reviews have built and gives "
            "patients a way to book while they are looking."
        )
        cta = "I can take a look and send a short recovery checklist. Want me to?"
    else:
        finding, kind = email_finding(findings, score if isinstance(score, int) else 0)
        if finding:
            observation, value = email_problem(finding, kind, audit)
            email_evidence = (
                f"{finding.get('title', '')}: {short_evidence(finding, 180)}"
            )
        else:
            observation = "There is one issue on the site worth fixing."
            value = "Small fixes make the site easier to use for patients who are already looking."
        if proof:
            observation = f"{proof}{observation}"
        if isinstance(score, int) and score >= 80:
            observation = f"Your site is already in good shape. {observation}"
            cta = "I can send the one change I would make first. Want it?"
        else:
            cta = email_cta(kind)
        subject = f"{short} {EMAIL_SUBJECT_SUFFIX.get(kind, 'site')}"

    observation = email_sentence(observation)
    value = email_sentence(value)
    signature = [
        clean_text(sender.get("name")) or "[Your name]",
        clean_text(sender.get("company")),
        clean_text(sender.get("address")) or "[Your postal address]",
    ]
    signature = [line for line in signature if line]
    body = "\n".join(
        [
            greeting,
            "",
            observation,
            "",
            value,
            "",
            cta,
            "",
            *signature,
            "",
            'Not relevant? Reply "no" and I won\'t follow up.',
        ]
    )

    if not email:
        email_status = "no_published_email"
        notes = "No published email. Use phone or the contact form; do not send until an address is found."
    elif buyer:
        email_status = "ready"
        notes = (
            f"Addressed to {buyer.get('name')} ({buyer.get('role')}). "
            "Verify the role and source before sending."
        )
    else:
        email_status = "ready"
        notes = "Generic business inbox. Ask for the owner or practice manager; no named buyer was verified."
    if isinstance(score, int) and score >= 80:
        notes += " Site is already functional; keep this light."
    return subject.lower(), body, email_status, notes, email_evidence


def best_route(dm: dict) -> str:
    routes = dm.get("contact_routes") or []
    if not routes:
        return ""
    for route in routes:
        if route.get("route") in ("linkedin", "email"):
            return f"{route['route']}: {route['value']} ({route.get('note', '')})"
    route = routes[0]
    return f"{route['route']}: {route['value']} ({route.get('note', '')})"


def dm_summary(dm: dict) -> str:
    if not dm:
        return "Not researched"
    cands = dm.get("candidates") or []
    if not cands:
        return "No verified name found; use role-based outreach"
    senior = [c for c in cands if c.get("seniority") in ("owner", "executive", "management")]
    listed = senior or cands
    summary = "; ".join(
        f"{c['name']} ({c.get('role', '')}, {c.get('confidence', '')})"
        for c in listed[:4]
    )
    if not senior and cands:
        return f"No owner/manager verified; team contacts: {summary}"
    return summary


def business_rows(businesses, audits, dms, sender: dict | None = None) -> tuple[list[dict], list[dict]]:
    sender = sender or {}
    audit_by = {key(a.get("business_name")): a for a in audits}
    dm_by = {key(d.get("business_name")): d for d in dms}
    all_rows = []
    finding_rows = []

    for business in businesses:
        name = clean_text(business.get("name"))
        k = key(name)
        audit = audit_by.get(k, {})
        dm = dm_by.get(k, {})
        findings = audit.get("findings") or []
        top = top_findings(findings, 5)
        offer = recommended_offer(audit) if audit else ""
        hook = outreach_hook(business, audit, findings) if audit else ""
        email_subject, email_body, email_status, email_notes, email_evidence = cold_email(
            business, audit, dm, sender
        ) if audit else (
            "",
            "",
            "no_audit",
            "No audit data; run the audit before writing email.",
            "",
        )

        all_rows.append(
            {
                "business_name": name,
                "category": clean_text(business.get("category")),
                "location": clean_text(business.get("location") or business.get("city")),
                "address": clean_text(business.get("address")),
                "phone": clean_text(business.get("phone")),
                "email": primary_email(business, audit),
                "website": clean_text(business.get("website")),
                "website_type": audit.get("website_type", ""),
                "website_status": audit.get("website_status", ""),
                "website_status_label": STATUS_LABELS.get(audit.get("website_status", ""), audit.get("website_status", "")),
                "http_status": audit.get("http_status", ""),
                "audit_score": audit.get("score", ""),
                "grade": audit.get("grade", ""),
                "opportunity_score": audit.get("opportunity_score", ""),
                "priority": audit.get("priority", ""),
                "qualification": qualification(audit) if audit else "",
                "recommended_offer": offer,
                "outreach_hook": hook,
                "opening_line": opening_line(business, audit, hook) if audit else "",
                "cold_email_subject": email_subject,
                "cold_email_body": email_body,
                "cold_email_status": email_status,
                "email_notes": email_notes,
                "email_evidence": email_evidence,
                "top_issues": "; ".join(f"{f.get('severity')}: {f.get('title')}" for f in top),
                "strengths": "; ".join((audit.get("strengths") or [])[:6]),
                "decision_makers": dm_summary(dm),
                "best_contact_route": best_route(dm),
                "rating": business.get("rating", ""),
                "reviews": business.get("reviews", ""),
                "place_url": business.get("place_url", ""),
                "query": business.get("query", ""),
                "verified_at": audit.get("verified_at", ""),
            }
        )

        for finding in findings:
            finding_rows.append(
                {
                    "business_name": name,
                    "website": audit.get("website_url", ""),
                    "score": audit.get("score", ""),
                    "priority": audit.get("priority", ""),
                    "category": finding.get("category", ""),
                    "severity": finding.get("severity", ""),
                    "title": finding.get("title", ""),
                    "evidence": finding.get("evidence", ""),
                    "why": finding.get("why", ""),
                    "fix": finding.get("fix", ""),
                    "effort": finding.get("effort", ""),
                    "page": finding.get("page", ""),
                }
            )

    all_rows.sort(
        key=lambda r: (
            PRIORITY_ORDER.get(r["priority"], 9),
            -(r["opportunity_score"] if isinstance(r["opportunity_score"], (int, float)) else 0),
            r["business_name"].lower(),
        )
    )
    finding_rows.sort(
        key=lambda r: (
            PRIORITY_ORDER.get(r["priority"], 9),
            SEVERITY_ORDER.get(r["severity"], 9),
            r["business_name"].lower(),
        )
    )
    return all_rows, finding_rows


def dm_rows(dms: list[dict]) -> list[dict]:
    rows = []
    for dm in dms:
        for cand in dm.get("candidates") or []:
            rows.append(
                {
                    "business_name": dm.get("business_name", ""),
                    "name": cand.get("name", ""),
                    "role": cand.get("role", ""),
                    "seniority": cand.get("seniority", ""),
                    "confidence": cand.get("confidence", ""),
                    "email": cand.get("email", ""),
                    "email_status": cand.get("email_status", ""),
                    "linkedin": cand.get("linkedin", ""),
                    "source_type": cand.get("source_type", ""),
                    "source_url": cand.get("source_url", ""),
                    "evidence": cand.get("evidence", ""),
                }
            )
    return rows


def md_escape(value) -> str:
    return clean_text(value).replace("|", "\\|")


def write_markdown(path: Path, businesses, audits, dms, all_rows, summary) -> None:
    audit_by = {key(a.get("business_name")): a for a in audits}
    dm_by = {key(d.get("business_name")): d for d in dms}
    lines: list[str] = []
    lines.append("# Business Lead Intelligence Report")
    lines.append("")
    lines.append(f"Generated: {utc_now()}")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append(f"- **Businesses discovered:** {summary['total_businesses']}")
    lines.append(f"- **Websites audited:** {summary['audited']}")
    lines.append(f"- **Website opportunity leads:** {summary['opportunity_leads']}")
    lines.append(f"- **Hot / warm / nurture:** {summary['hot']} / {summary['warm']} / {summary['nurture']}")
    lines.append(f"- **Average website score:** {summary['average_score']}/100")
    lines.append(f"- **Named decision makers found:** {summary['decision_makers_found']}")
    lines.append(f"- **Cold emails ready / missing recipient:** {summary['emails_ready']} / {summary['emails_missing']}")
    lines.append("")
    lines.append("### Website status breakdown")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---:|")
    for label, count in summary["status_counts"].items():
        lines.append(f"| {label} | {count} |")
    lines.append("")
    lines.append("### Priority queue")
    lines.append("")
    lines.append("| # | Business | Score | Priority | Main opportunity | Phone | Website |")
    lines.append("|---:|---|---:|---|---|---|---|")
    for i, row in enumerate(all_rows[:20], 1):
        lines.append(
            f"| {i} | {md_escape(row['business_name'])} | {row['audit_score']} | "
            f"{row['priority']} | {md_escape(row['recommended_offer'])} | "
            f"{md_escape(row['phone']) or '—'} | {md_escape(row['website']) or '—'} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, business in enumerate(all_rows, 1):
        k = key(business["business_name"])
        audit = audit_by.get(k, {})
        dm = dm_by.get(k, {})
        findings = sorted(
            audit.get("findings") or [],
            key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "low"), 9), f.get("category", "")),
        )
        lines.append(f"## {i}. {business['business_name']}")
        lines.append("")
        lines.append(
            f"**Score:** {audit.get('score', '—')}/100 ({audit.get('grade', '—')}) · "
            f"**Priority:** {audit.get('priority', '—')} · "
            f"**Qualification:** {business.get('qualification', '—')} · "
            f"**Opportunity score:** {audit.get('opportunity_score', '—')}/100"
        )
        lines.append("")
        lines.append(f"- **Category:** {business.get('category') or '—'}")
        lines.append(f"- **Address:** {business.get('address') or '—'}")
        lines.append(f"- **Phone:** {business.get('phone') or '—'}")
        lines.append(f"- **Email:** {business.get('email') or '—'}")
        lines.append(f"- **Website:** {business.get('website') or '—'}")
        lines.append(f"- **Website status:** {business.get('website_status_label') or '—'}")
        if business.get("place_url"):
            lines.append(f"- **Google Maps:** {business['place_url']}")
        if business.get("rating"):
            lines.append(f"- **Google rating:** {business.get('rating')} ({business.get('reviews', '—')} reviews)")
        lines.append("")
        lines.append(f"**Recommended offer:** {business.get('recommended_offer') or '—'}")
        lines.append("")

        lines.append("### Outreach angle")
        lines.append("")
        lines.append(f"- **Observable:** {business.get('outreach_hook') or '—'}")
        lines.append(f"- **Angle:** {business.get('recommended_offer') or '—'}")
        lines.append(f"- **Opening line:** {business.get('opening_line') or '—'}")
        lines.append("")

        lines.append("### Cold email")
        lines.append("")
        lines.append(f"- **Send to:** {business.get('email') or 'No published email'}")
        lines.append(f"- **Status:** {business.get('cold_email_status') or '—'}")
        lines.append(f"- **Subject:** {business.get('cold_email_subject') or '—'}")
        lines.append("")
        lines.append("```text")
        lines.append(business.get("cold_email_body") or "No draft available.")
        lines.append("```")
        lines.append("")
        lines.append(f"- **Note:** {business.get('email_notes') or '—'}")
        lines.append(f"- **Evidence:** {business.get('email_evidence') or '—'}")
        lines.append("")

        lines.append("### Contact channels")
        lines.append("")
        channels = audit.get("contact_channels") or {}
        lines.append(f"- Phone: {business.get('phone') or '—'}")
        lines.append(f"- Email: {', '.join(channels.get('emails') or []) or '—'}")
        lines.append(f"- WhatsApp: {', '.join(channels.get('whatsapp') or []) or '—'}")
        lines.append(f"- Contact form: {'Yes' if channels.get('has_contact_form') else 'No'}")
        lines.append(f"- Click-to-call link: {'Yes' if channels.get('has_tel_link') else 'No'}")
        social = channels.get("social") or {}
        if social:
            lines.append("- Social: " + ", ".join(f"{k}: {v}" for k, v in social.items()))
        lines.append("")

        lines.append("### What works")
        lines.append("")
        if audit.get("strengths"):
            for strength in audit["strengths"]:
                lines.append(f"- {strength}")
        else:
            lines.append("- No clear strengths detected by the automated checks.")
        lines.append("")

        lines.append("### What needs working — prioritized findings")
        lines.append("")
        if not findings:
            lines.append("No automated findings. Verify manually in a browser before treating this as a clean site.")
            lines.append("")
        for j, finding in enumerate(findings, 1):
            page = finding.get("page") or audit.get("final_url") or audit.get("website_url") or ""
            lines.append(f"#### {j}. [{finding.get('severity', '').upper()}] {finding.get('title', '')} — {finding.get('category', '')}")
            lines.append("")
            lines.append(f"- **Where:** {page or '—'}")
            lines.append(f"- **Evidence:** {finding.get('evidence') or '—'}")
            lines.append(f"- **Why it matters:** {finding.get('why') or '—'}")
            lines.append(f"- **How to fix:** {finding.get('fix') or '—'}")
            lines.append(f"- **Effort:** {finding.get('effort') or '—'}")
            lines.append("")

        lines.append("### Decision makers")
        lines.append("")
        cands = dm.get("candidates") or []
        senior = [c for c in cands if c.get("seniority") in ("owner", "executive", "management")]
        clinical = [c for c in cands if c.get("seniority") == "clinical"]
        if not dm:
            lines.append("Decision-maker research was not run for this business.")
            lines.append("")
        elif senior:
            lines.append("| Name | Role | Confidence | Email | Source |")
            lines.append("|---|---|---|---|---|")
            for cand in senior[:8]:
                lines.append(
                    f"| {md_escape(cand.get('name'))} | {md_escape(cand.get('role'))} | "
                    f"{md_escape(cand.get('confidence'))} | {md_escape(cand.get('email')) or '—'} | "
                    f"{md_escape(cand.get('source_url'))} |"
                )
            lines.append("")
            if clinical:
                lines.append("Clinical contacts (not confirmed decision makers): " + ", ".join(
                    f"{md_escape(c.get('name'))} ({md_escape(c.get('role'))})" for c in clinical[:4]
                ))
                lines.append("")
            lines.append(f"**Best contact route:** {best_route(dm) or '—'}")
        elif cands:
            lines.append(
                "No owner, executive, or manager was verified publicly. "
                "The people below are clinical/team contacts — use them for context, "
                "but ask for the owner or practice manager when you reach out."
            )
            lines.append("")
            lines.append("| Name | Role | Confidence | Email | Source |")
            lines.append("|---|---|---|---|---|")
            for cand in cands[:8]:
                lines.append(
                    f"| {md_escape(cand.get('name'))} | {md_escape(cand.get('role'))} | "
                    f"{md_escape(cand.get('confidence'))} | {md_escape(cand.get('email')) or '—'} | "
                    f"{md_escape(cand.get('source_url'))} |"
                )
            lines.append("")
            lines.append(f"**Best contact route:** {best_route(dm) or '—'}")
        else:
            lines.append("No named decision maker was verified from public sources.")
            lines.append("")
            lines.append("**Suggested routes:**")
            lines.append("")
            lines.append("- Call the listed number and ask for the owner, founder, practice manager, or marketing manager by role.")
            lines.append("- Use the website contact form and address the message to the owner/manager.")
            if dm.get("search_urls"):
                lines.append(f"- LinkedIn people search: {dm['search_urls'].get('linkedin_people', '')}")
        lines.append("")
        if dm.get("contact_routes"):
            lines.append("**All contact routes:**")
            lines.append("")
            for route in dm["contact_routes"]:
                lines.append(f"- {route.get('route')}: {route.get('value')} — {route.get('note', '')}")
            lines.append("")

        lines.append("### Evidence, sources & limitations")
        lines.append("")
        if audit.get("checks"):
            p = audit.get("checks", {}).get("page", {})
            lines.append(
                f"- HTTP {audit.get('http_status', '—')} · {p.get('word_count', '—')} words · "
                f"{p.get('images', '—')} images · {p.get('external_scripts', '—')} external scripts"
            )
            if p.get("title"):
                lines.append(f"- Title: {p.get('title')}")
            if p.get("meta_description"):
                lines.append(f"- Meta description: {p.get('meta_description')}")
            if p.get("schema_types"):
                lines.append(f"- Schema: {', '.join(p.get('schema_types'))}")
            if audit.get("pages_checked"):
                lines.append("- Pages checked: " + ", ".join(
                    f"{x.get('label') or 'page'} ({x.get('status')})" for x in audit["pages_checked"]
                ))
        for limitation in (audit.get("limitations") or []) + (dm.get("limitations") or []):
            lines.append(f"- Limitation: {limitation}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_cold_emails(path: Path, rows: list[dict]) -> None:
    ready = [r for r in rows if r.get("cold_email_status") == "ready"]
    missing = [r for r in rows if r.get("cold_email_status") != "ready"]
    lines: list[str] = []
    lines.append("# Cold Email Drafts")
    lines.append("")
    lines.append(f"Generated: {utc_now()}")
    lines.append("")
    lines.append(
        "First-touch drafts built only from verified Maps data and audit findings. "
        "Review each email before sending; do not add claims, results, or names that are not in the report."
    )
    lines.append("")
    lines.append(f"- **Drafts with a published recipient:** {len(ready)}")
    lines.append(f"- **Drafts without a published email:** {len(missing)}")
    lines.append("")
    lines.append("## Before sending")
    lines.append("")
    lines.append("- Replace any bracketed sender placeholders (name, company, postal address).")
    lines.append("- Verify the recipient and the finding on the source page.")
    lines.append("- Use a secondary sending domain, verify the address list, and keep the opt-out line.")
    lines.append("- Do not send a draft marked `no_published_email` until you find a real address.")
    lines.append("")
    for label, group in (("Ready to send", ready), ("No published email", missing)):
        if not group:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for row in group:
            lines.append(f"### {row.get('business_name', '')}")
            lines.append("")
            lines.append(f"- **To:** {row.get('email') or 'No published email'}")
            lines.append(f"- **Status:** {row.get('cold_email_status') or '—'}")
            lines.append(f"- **Phone:** {row.get('phone') or '—'}")
            lines.append(f"- **Subject:** {row.get('cold_email_subject') or '—'}")
            lines.append("")
            lines.append("```text")
            lines.append(row.get("cold_email_body") or "No draft available.")
            lines.append("```")
            lines.append("")
            lines.append(f"**Note:** {row.get('email_notes') or '—'}")
            lines.append("")
            lines.append(f"**Evidence:** {row.get('email_evidence') or '—'}")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build lead deliverables from pipeline outputs.")
    parser.add_argument("--businesses", required=True)
    parser.add_argument("--audits", required=True)
    parser.add_argument("--decision-makers", default="")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--prefix", default="leads")
    parser.add_argument("--sender-name", default="[Your name]")
    parser.add_argument("--sender-company", default="")
    parser.add_argument("--sender-address", default="[Your postal address]")
    args = parser.parse_args()

    payload = load_json(args.businesses, [])
    businesses = payload.get("businesses", []) if isinstance(payload, dict) else payload
    businesses = dedupe_businesses(businesses)

    audit_payload = load_json(args.audits, {})
    audits = audit_payload.get("audits", []) if isinstance(audit_payload, dict) else audit_payload

    dms = []
    if args.decision_makers:
        dm_payload = load_json(args.decision_makers, {})
        dms = dm_payload.get("results", []) if isinstance(dm_payload, dict) else dm_payload

    rows, findings = business_rows(
        businesses,
        audits,
        dms,
        {
            "name": args.sender_name,
            "company": args.sender_company,
            "address": args.sender_address,
        },
    )
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_cols = [
        "business_name", "category", "location", "address", "phone", "email", "website",
        "website_type", "website_status", "website_status_label", "http_status",
        "audit_score", "grade", "opportunity_score", "priority", "qualification",
        "recommended_offer", "outreach_hook", "opening_line", "cold_email_subject",
        "cold_email_body", "cold_email_status", "email_notes", "email_evidence",
        "top_issues", "strengths",
        "decision_makers", "best_contact_route", "rating", "reviews", "place_url",
        "query", "verified_at",
    ]
    save_csv(out / f"{args.prefix}_all_leads.csv", rows, all_cols)

    opportunity = [r for r in rows if r["website_status"] != "up" or (isinstance(r["audit_score"], int) and r["audit_score"] < 75)]
    save_csv(out / f"{args.prefix}_website_opportunity_leads.csv", opportunity, all_cols)

    finding_cols = [
        "business_name", "website", "score", "priority", "category", "severity",
        "title", "evidence", "why", "fix", "effort", "page",
    ]
    save_csv(out / f"{args.prefix}_site_findings.csv", findings, finding_cols)

    dm_cols = [
        "business_name", "name", "role", "seniority", "confidence", "email",
        "email_status", "linkedin", "source_type", "source_url", "evidence",
    ]
    save_csv(out / f"{args.prefix}_decision_makers.csv", dm_rows(dms), dm_cols)

    status_counts = Counter(
        STATUS_LABELS.get(r["website_status"], r["website_status"] or "unknown") for r in rows
    )
    scores = [r["audit_score"] for r in rows if isinstance(r["audit_score"], int)]
    summary = {
        "generated_at": utc_now(),
        "total_businesses": len(rows),
        "audited": len(audits),
        "opportunity_leads": len(opportunity),
        "hot": sum(1 for r in rows if r["qualification"] == "hot"),
        "warm": sum(1 for r in rows if r["qualification"] == "warm"),
        "nurture": sum(1 for r in rows if r["qualification"] == "nurture"),
        "average_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "decision_makers_found": sum(len(d.get("candidates") or []) for d in dms),
        "emails_ready": sum(1 for r in rows if r["cold_email_status"] == "ready"),
        "emails_missing": sum(1 for r in rows if r["cold_email_status"] != "ready"),
        "status_counts": dict(status_counts.most_common()),
    }
    save_json(out / f"{args.prefix}_run_summary.json", summary)
    save_json(out / f"{args.prefix}_all_leads.json", {"generated_at": utc_now(), "leads": rows})
    write_markdown(out / f"{args.prefix}_FINDINGS.md", businesses, audits, dms, rows, summary)
    write_cold_emails(out / f"{args.prefix}_cold_emails.md", rows)

    print(f"Wrote deliverables to {out}/")
    print(f"Cold email drafts: {out}/{args.prefix}_cold_emails.md")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
