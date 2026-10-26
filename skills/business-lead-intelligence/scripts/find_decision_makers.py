#!/usr/bin/env python3
"""Find likely decision makers for a business using public, lawful sources.

Sources:
  * the business's own team/about/leadership pages
  * JSON-LD Person records
  * public search-result titles/snippets (DuckDuckGo HTML, Bing fallback)
  * public social profile links exposed by the business

It never logs into, scrapes, or bypasses access controls on LinkedIn or any
other network. Search results are evidence, not proof: every candidate carries
a confidence level and source URL for human verification.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TMP_DIR = Path(tempfile.gettempdir()) / "opencode"

from common import (
    EMAIL_RE,
    clean_text,
    domain_of,
    load_json,
    save_json,
    utc_now,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ROLE_PATTERNS = [
    (r"\bco[-\s]?founder\b", "Co-Founder", "owner"),
    (r"\bfounder\b", "Founder", "owner"),
    (r"\bowner\b", "Owner", "owner"),
    (r"\bproprietor\b", "Proprietor", "owner"),
    (r"\bmanaging director\b", "Managing Director", "executive"),
    (r"\bchief executive(?: officer)?\b|\bceo\b", "CEO", "executive"),
    (r"\bchief operating officer\b|\bcoo\b", "COO", "executive"),
    (r"\bchief marketing officer\b|\bcmo\b", "CMO", "executive"),
    (r"\bfinance director\b", "Finance Director", "executive"),
    (r"\bdirector\b", "Director", "executive"),
    (r"\bprincipal\b", "Principal", "executive"),
    (r"\bpartner\b", "Partner", "executive"),
    (r"\bpresident\b", "President", "executive"),
    (r"\bchair(?:man|person|woman)\b", "Chair", "executive"),
    (r"\bgeneral manager\b|\bgm\b", "General Manager", "management"),
    (r"\bpractice manager\b", "Practice Manager", "management"),
    (r"\bclinic manager\b", "Clinic Manager", "management"),
    (r"\boffice manager\b", "Office Manager", "management"),
    (r"\boperations manager\b", "Operations Manager", "management"),
    (r"\bmarketing (?:manager|head)\b|\bhead of marketing\b", "Marketing Manager", "management"),
    (r"\bsales (?:manager|head)\b|\bhead of sales\b", "Sales Manager", "management"),
    (r"\bbusiness development manager\b", "Business Development Manager", "management"),
    (r"\bhr manager\b|\bhuman resources manager\b", "HR Manager", "management"),
    (r"\bit manager\b", "IT Manager", "management"),
    (r"\bhead of (?:orthodontics|clinical|operations|digital|marketing)\b", None, "management"),
    (r"\bfront desk(?: manager)?\b|\breception(?:ist|ionist)\b", "Front Desk", "support"),
    (r"\bmanager\b", "Manager", "management"),
    (r"\bconsultant (?:orthodontist|prosthodontist|endodontist|periodontist|surgeon|physician|dentist)\b", None, "clinical"),
    (r"\b(?:orthodontist|prosthodontist|endodontist|periodontist|maxillofacial surgeon|oral surgeon|aesthetic physician|aesthetic dentist|restorative dentist|associate dentist|general dentist|dentist|physician|surgeon)\b", None, "clinical"),
    (r"\bdoctor\b|\bdr\.?\b", "Doctor", "clinical"),
]

CLINICAL_TITLES = re.compile(
    r"\b(?:consultant\s+)?(?:orthodontist|prosthodontist|endodontist|periodontist|"
    r"maxillofacial surgeon|oral surgeon|aesthetic physician|aesthetic dentist|"
    r"restorative dentist|associate dentist|general dentist|dentist|physician|surgeon)\b",
    re.I,
)
HEAD_OF = re.compile(r"\bhead of (orthodontics|clinical|operations|digital|marketing)\b", re.I)

BAD_NAME_WORDS = {
    # roles / seniority
    "owner", "founder", "cofounder", "ceo", "coo", "cmo", "director", "manager",
    "president", "chairman", "chairperson", "partner", "principal", "proprietor",
    "administrator", "consultant", "specialist", "doctor", "dr", "prof", "professor",
    "orthodontist", "prosthodontist", "endodontist", "periodontist", "surgeon",
    "physician", "dentist", "associate", "restorative", "aesthetic", "general",
    "clinical", "medical", "dental", "head", "lead", "senior", "junior",
    # credentials
    "bds", "fcps", "rcse", "rcsed", "chpe", "dds", "phd", "mba", "mbbs", "frcs",
    "md", "ms", "rds", "c", "implant", "ortho", "orth", "morth", "pak", "uk",
    # institutions / page text
    "university", "college", "hospital", "institute", "academy", "school",
    "center", "centre", "clinic", "care", "trust", "foundation", "society",
    "read", "more", "view", "team", "our", "the", "and", "of", "at", "for",
    "with", "services", "contact", "about", "home", "book", "appointment",
    "meet", "welcome", "profile", "biography", "experience", "years", "member",
    "american", "association", "new", "york", "london", "karachi", "lahore",
    "islamabad", "rawalpindi", "pakistan", "abuja", "dubai",
    "certified", "facial", "aesthetician", "aacme", "hcm", "dph", "int",
    "assistant", "memorial", "where", "excellence", "meets", "perfection",
    "smile", "smiles", "trusted", "families", "across", "quality", "comfort",
    "advanced", "modern", "best", "top", "welcome", "exceptional", "skill",
    "precision", "attention", "detail", "decade", "seeing", "patient", "patients",
    "treatment", "treatments", "morth", "rcsed", "additional", "message",
    "email", "address", "phone", "date", "select", "age", "comments", "comment",
    "name", "appointment", "choose", "form", "why", "what", "when", "how",
    "root", "canal", "surgery", "dentistry", "operative", "maxillofacial",
    "department", "unit", "ward", "booking", "schedule",
    # locations / page chrome / marketing phrases that look like names
    "oral", "health", "front", "desk", "reception", "receptionist", "model",
    "town", "city", "street", "road", "avenue", "block", "phase", "sector",
    "neighbourhood", "neighborhood", "copyright", "developed", "designed",
    "powered", "confident", "you", "xtreme", "also", "but", "this", "that",
    "these", "those",
}

TITLE_PREFIX = re.compile(
    r"^(?:(?:dr|prof|professor|mr|mrs|ms|miss|engr|sahibzada|hafiz|maulana)\.?\s+)+",
    re.I,
)
NAME_RE = re.compile(r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z'’.-]{1,25}){1,3})\b")
SEGMENT_SPLIT = re.compile(r"\s*(?:\||,|;|–|—|\s-\s|:|\n|•|·)\s*")
TRAILING_ROLE = re.compile(
    r"\s+(?:managing(?:\s+director)?|director|ceo|coo|cmo|founder|owner|manager|"
    r"consultant|orthodontist|prosthodontist|endodontist|periodontist|surgeon|"
    r"physician|dentist|doctor|hospital|clinic|head|lead|principal|partner)\b.*$",
    re.I,
)


def strip_trailing_role(name: str) -> str:
    return clean_text(TRAILING_ROLE.sub("", clean_text(name)))


def role_for(text: str) -> tuple[str, str] | None:
    text = clean_text(text)
    lower = text.lower()
    for pattern, label, seniority in ROLE_PATTERNS:
        if re.search(pattern, lower, re.I):
            if label is None:
                head = HEAD_OF.search(text)
                if head:
                    label = "Head of " + head.group(1).title()
                else:
                    clinical = CLINICAL_TITLES.search(text)
                    label = clinical.group(0).title() if clinical else "Clinician"
            return label, seniority
    return None


def strip_titles(name: str) -> str:
    previous = None
    value = clean_text(name)
    while value != previous:
        previous = value
        value = TITLE_PREFIX.sub("", value).strip(" .,-|")
    return value


def name_key(name: str) -> str:
    value = strip_titles(name).lower()
    value = re.sub(r"[^a-z\s]", "", value)
    return " ".join(value.split())


def plausible_name(candidate: str, business_name: str) -> bool:
    value = strip_titles(candidate)
    value = clean_text(re.sub(r"['’]s$", "", clean_text(value)))
    if not value or len(value) > 44:
        return False
    if "." in value:
        return False
    if re.search(r"[&/\\\d@()\[\]+]", value):
        return False
    if re.search(r"\b[A-Z]\.[A-Za-z]", value):
        return False
    words = value.split()
    if not 2 <= len(words) <= 4:
        return False
    if any(w.lower().strip(".") in BAD_NAME_WORDS for w in words):
        return False
    if all(w.isupper() for w in words):
        return False
    if any(len(w.strip(".")) < 2 for w in words):
        return False
    if business_name and (value.lower() in business_name.lower() or business_name.lower() in value.lower()):
        return False
    # Each token should look like a proper name, not a sentence fragment.
    for word in words:
        base = word.strip(".-'’")
        if not base or not base[0].isupper():
            return False
        if any(ch.isdigit() for ch in base):
            return False
    return True


def extract_name_from_block(block: str, business_name: str) -> str:
    block = clean_text(block)
    if not block:
        return ""

    # 1) A titled person is the strongest signal: Dr./Prof./Mr./Ms. Name
    for match in re.finditer(
        r"\b((?:Dr|Prof|Professor|Mr|Mrs|Ms|Miss|Engr)\.?\s+"
        r"[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})",
        block,
    ):
        candidate = strip_trailing_role(clean_text(match.group(1)))
        if plausible_name(candidate, business_name):
            return candidate

    # 2) Split on separators and look for a proper-name segment.
    for segment in SEGMENT_SPLIT.split(block):
        segment = clean_text(segment)
        if not segment or role_for(segment):
            continue
        candidate = strip_titles(segment)
        candidate = re.sub(
            r"\s+(?:read more|view profile|learn more|book now|contact us)$", "", candidate, flags=re.I
        )
        candidate = strip_trailing_role(candidate)
        if plausible_name(candidate, business_name):
            return candidate

    # 3) Last resort: a proper-name run that is not part of a role phrase.
    for match in NAME_RE.finditer(block):
        candidate = strip_trailing_role(match.group(1))
        if plausible_name(candidate, business_name):
            return candidate
    return ""


def fetch(session: requests.Session, url: str, timeout: float = 15.0):
    try:
        resp = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and "html" in resp.headers.get("content-type", "html"):
            return resp
    except Exception:
        return None
    return None


def company_pages(session: requests.Session, website: str, timeout: float = 15.0) -> list[dict]:
    if not website:
        return []
    if not re.match(r"^https?://", website):
        website = "https://" + website
    host = domain_of(website)
    if not host:
        return []
    home = fetch(session, website, timeout)
    if not home:
        return []
    soup = BeautifulSoup(home.text, "html.parser")
    keywords = ["team", "about", "leadership", "management", "our-team", "staff", "people", "founders", "doctors", "faculty"]
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(home.url, href)
        if domain_of(full) != host:
            continue
        text = (full + " " + a.get_text(" ", strip=True)).lower()
        for i, key in enumerate(keywords):
            if key in text:
                scored[full] = max(scored.get(full, 0), 10 - i)
                break
    pages = [{"url": home.url, "label": "Home", "html": home.text}]
    for url in sorted(scored, key=scored.get, reverse=True)[:4]:
        resp = fetch(session, url, timeout)
        if resp:
            pages.append({"url": resp.url, "label": "Team/About", "html": resp.text})
    return pages


def extract_from_jsonld(soup: BeautifulSoup, page_url: str, business_name: str) -> list[dict]:
    out: list[dict] = []
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
            if isinstance(item.get("@graph"), list):
                stack.extend(item["@graph"])
            obj_type = item.get("@type")
            types = obj_type if isinstance(obj_type, list) else [obj_type]
            if any(str(t).lower() == "person" for t in types):
                name = clean_text(item.get("name"))
                role = clean_text(item.get("jobTitle") or item.get("description"))
                if name and role and plausible_name(name, business_name):
                    out.append(
                        {
                            "name": name,
                            "role": role,
                            "seniority": role_for(role)[1] if role_for(role) else "unknown",
                            "source_type": "company_jsonld",
                            "source_url": page_url,
                            "evidence": f"JSON-LD Person: {name} — {role}",
                            "confidence": "high",
                            "email": clean_text(item.get("email")).replace("mailto:", ""),
                            "linkedin": clean_text(item.get("sameAs")) if "linkedin.com" in clean_text(item.get("sameAs")) else "",
                        }
                    )
    return out


def extract_from_html(html: str, page_url: str, business_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    out.extend(extract_from_jsonld(soup, page_url, business_name))

    blocks: list[tuple[str, object]] = []
    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "td", "div", "span", "strong", "b", "a"]):
        if node.find(["h1", "h2", "h3", "h4", "h5", "p", "li", "div", "table", "section"]):
            continue
        text = clean_text(node.get_text(" ", strip=True))
        if 10 <= len(text) <= 280:
            blocks.append((text, node))

    body_text = soup.get_text("\n", strip=True)
    for line in body_text.splitlines():
        line = clean_text(line)
        if 10 <= len(line) <= 280:
            blocks.append((line, None))

    seen_blocks: set[str] = set()
    for block, node in blocks:
        if block in seen_blocks:
            continue
        seen_blocks.add(block)
        context = block
        if node is not None and node.parent is not None:
            parent_text = clean_text(node.parent.get_text(" ", strip=True))
            if len(parent_text) <= 500:
                context = block + " | " + parent_text
        role_hit = role_for(context) or role_for(block)
        if not role_hit:
            continue
        role_label, seniority = role_hit

        form_text = re.search(
            r"\b(select|appointment|your name|email address|phone no|message|choose|book|form|comments|age|date)\b",
            block,
            re.I,
        )
        strong_role = re.search(
            r"\b(owner|founder|co[ -]?founder|ceo|managing director|director|manager|principal|partner)\b",
            block,
            re.I,
        )
        if form_text and not strong_role:
            continue
        if re.search(r"(?:developed|designed|powered)\s+by|[©]|copyright|all rights reserved", block, re.I):
            continue

        if seniority == "clinical":
            if re.search(
                r"\b(review|testimonial|patient story|seeing dr|recommend|exceptional|best dentist|got my|friend'?s treatment|i found dr)\b",
                context,
                re.I,
            ):
                continue
            titled = re.findall(r"\b(?:Dr|Prof|Professor)\.?\s+[A-Z]", block)
            if not titled or len(titled) > 2:
                continue
            if not re.search(r"\b(?:Dr|Prof|Professor)\.?\s+[A-Z]", block):
                continue

        name = extract_name_from_block(block, business_name)
        if not name:
            name = extract_name_from_block(context, business_name)
        if not name:
            continue
        emails = EMAIL_RE.findall(block)
        linkedin = ""
        if node is not None:
            for a in node.find_all("a", href=True):
                if "linkedin.com" in a["href"].lower():
                    linkedin = a["href"]
                    break
            if not linkedin and node.parent is not None:
                for a in node.parent.find_all("a", href=True):
                    if "linkedin.com" in a["href"].lower():
                        linkedin = a["href"]
                        break
        out.append(
            {
                "name": name,
                "role": role_label,
                "seniority": seniority,
                "source_type": "company_team_page",
                "source_url": page_url,
                "evidence": context[:300],
                "confidence": "high",
                "email": emails[0] if emails else "",
                "linkedin": linkedin,
            }
        )
    return out


def decode_bing_url(url: str) -> str:
    if "bing.com/ck/a" not in url:
        return url
    try:
        query = parse_qs(urlparse(url).query)
        raw = (query.get("u") or [""])[0]
        if raw.startswith("a1"):
            raw = raw[2:]
        raw += "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8", errors="ignore")
        if decoded.startswith("http"):
            return decoded
    except Exception:
        pass
    return url


def search_duckduckgo(session: requests.Session, query: str, timeout: float = 15.0) -> list[dict]:
    try:
        resp = session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={**HEADERS, "Referer": "https://html.duckduckgo.com/"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        out = []
        for result in soup.select("div.result"):
            link = result.select_one("a.result__a")
            snippet = result.select_one(".result__snippet")
            if not link:
                continue
            href = link.get("href", "")
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            out.append(
                {
                    "title": clean_text(link.get_text(" ", strip=True)),
                    "snippet": clean_text(snippet.get_text(" ", strip=True)) if snippet else "",
                    "url": href,
                }
            )
            if len(out) >= 8:
                break
        return out
    except Exception:
        return []


def search_bing(session: requests.Session, query: str, timeout: float = 15.0) -> list[dict]:
    try:
        resp = session.get(
            "https://www.bing.com/search",
            params={"q": query, "count": 10},
            headers=HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        out = []
        for li in soup.select("li.b_algo"):
            link = li.select_one("h2 a")
            snippet = li.select_one(".b_caption p") or li.select_one("p")
            if not link:
                continue
            out.append(
                {
                    "title": clean_text(link.get_text(" ", strip=True)),
                    "snippet": clean_text(snippet.get_text(" ", strip=True)) if snippet else "",
                    "url": decode_bing_url(link.get("href", "")),
                }
            )
            if len(out) >= 8:
                break
        return out
    except Exception:
        return []


def search_web(session: requests.Session, query: str, cache: dict) -> list[dict]:
    if query in cache:
        return cache[query]
    results = search_duckduckgo(session, query)
    if not results:
        results = search_bing(session, query)
    cache[query] = results
    return results


def business_tokens(name: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "clinic", "center", "centre", "hospital",
        "dental", "care", "studio", "salon", "gym", "school", "academy",
        "services", "solutions", "company", "pvt", "ltd", "private", "limited",
        "professionals", "professional", "group", "associates",
    }
    return [t.lower() for t in re.findall(r"[A-Za-z]{3,}", name) if t.lower() not in stop]


def relevant(result: dict, business_name: str, location: str) -> bool:
    blob = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
    if location and location.lower() not in blob:
        return False
    if business_name.lower() in blob:
        return True
    tokens = business_tokens(business_name)
    return len([t for t in tokens if t in blob]) >= 2


def candidates_from_search(results: list[dict], business_name: str, location: str) -> list[dict]:
    out: list[dict] = []
    for result in results:
        blob = f"{result['title']} . {result['snippet']}"
        if not relevant(result, business_name, location):
            continue
        for segment in re.split(r"\s*(?:\||·|•)\s*", blob):
            role_hit = role_for(segment)
            if not role_hit:
                continue
            name = extract_name_from_block(segment, business_name)
            if not name:
                continue
            source_type = "linkedin_search_result" if "linkedin.com/in/" in result["url"] else "web_search_result"
            confidence = "medium" if source_type == "linkedin_search_result" else "low"
            out.append(
                {
                    "name": name,
                    "role": role_hit[0],
                    "seniority": role_hit[1],
                    "source_type": source_type,
                    "source_url": result["url"],
                    "evidence": blob[:320],
                    "confidence": confidence,
                    "email": (EMAIL_RE.findall(blob) or [""])[0],
                    "linkedin": result["url"] if "linkedin.com/in/" in result["url"] else "",
                }
            )
            break
        if len(out) >= 8:
            break
    return out


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ca != cb),
            ))
        previous = current
    return previous[-1]


def dedupe_candidates(candidates: list[dict]) -> list[dict]:
    rank = {"high": 3, "medium": 2, "low": 1}
    order = {"owner": 0, "executive": 1, "management": 2, "clinical": 3, "support": 4, "unknown": 5}
    merged: dict[str, dict] = {}
    for cand in candidates:
        name = re.sub(r"['’]s$", "", clean_text(cand.get("name")))
        if not name or not plausible_name(name, ""):
            continue
        key = name_key(name)
        if not key:
            continue
        cand["name"] = name
        current = merged.get(key)
        if current is None:
            merged[key] = cand
            continue
        # Prefer the more senior role, then higher confidence.
        current_rank = (
            order.get(current.get("seniority"), 9),
            -rank.get(current.get("confidence"), 0),
        )
        new_rank = (
            order.get(cand.get("seniority"), 9),
            -rank.get(cand.get("confidence"), 0),
        )
        if new_rank < current_rank:
            merged[key] = cand
        elif not current.get("email") and cand.get("email"):
            current["email"] = cand["email"]
        elif not current.get("linkedin") and cand.get("linkedin"):
            current["linkedin"] = cand["linkedin"]
    # Collapse "John Smith" / "John Smith Jr" style duplicates, keeping the
    # longer, more specific name.
    keys = list(merged)
    for short in keys:
        if short not in merged:
            continue
        short_tokens = short.split()
        for long in keys:
            if long == short or long not in merged:
                continue
            long_tokens = long.split()
            if len(short_tokens) < len(long_tokens) and long_tokens[: len(short_tokens)] == short_tokens:
                # Merge any published contact details upward, then drop the short key.
                for field in ("email", "linkedin"):
                    if not merged[long].get(field) and merged[short].get(field):
                        merged[long][field] = merged[short][field]
                del merged[short]
                break

    # Merge likely spelling variants ("Zulfikar" / "Zulfiqar"): same leading
    # tokens and a one-character difference in the final token.
    changed = True
    while changed:
        changed = False
        keys = list(merged)
        for a in keys:
            if a not in merged:
                continue
            a_tokens = a.split()
            if len(a_tokens) < 2:
                continue
            for b in keys:
                if a == b or b not in merged:
                    continue
                b_tokens = b.split()
                if len(a_tokens) != len(b_tokens) or a_tokens[:-1] != b_tokens[:-1]:
                    continue
                if _levenshtein(a_tokens[-1], b_tokens[-1]) != 1:
                    continue
                a_rank = (
                    order.get(merged[a].get("seniority"), 9),
                    -rank.get(merged[a].get("confidence"), 0),
                )
                b_rank = (
                    order.get(merged[b].get("seniority"), 9),
                    -rank.get(merged[b].get("confidence"), 0),
                )
                keep, drop = (b, a) if b_rank < a_rank else (a, b)
                for field in ("email", "linkedin"):
                    if not merged[keep].get(field) and merged[drop].get(field):
                        merged[keep][field] = merged[drop][field]
                del merged[drop]
                changed = True
                break
            if changed:
                break

    return sorted(
        merged.values(),
        key=lambda c: (order.get(c.get("seniority"), 9), -rank.get(c.get("confidence"), 0), c["name"]),
    )


def contact_routes(business: dict, candidates: list[dict]) -> list[dict]:
    routes = []
    phone = clean_text(business.get("phone"))
    website = clean_text(business.get("website"))
    # Address outreach to an actual decision maker. A listed clinician is not
    # automatically the buyer, so fall back to a role-based ask instead.
    top = next(
        (c for c in candidates if c.get("seniority") in ("owner", "executive", "management")),
        None,
    )
    if phone:
        note = "Primary listed number; ask for the owner or manager by role."
        if top:
            note = f"Primary listed number; ask for {top['name']} ({top['role']})."
        routes.append({"route": "phone", "value": phone, "note": note})
    if website:
        note = "Use the form and address it to the owner or manager."
        if top:
            note = f"Use the form and address it to {top['name']} ({top['role']})."
        routes.append({"route": "website_contact_form", "value": website.rstrip("/") + "/contact", "note": note})
    if business.get("email"):
        routes.append({"route": "email", "value": business["email"], "note": "Published email, if present."})
    for cand in candidates[:3]:
        if cand.get("linkedin"):
            routes.append({"route": "linkedin", "value": cand["linkedin"], "note": f"{cand['name']} — {cand['role']} (verify profile before outreach)."})
        if cand.get("email"):
            routes.append({"route": "email", "value": cand["email"], "note": f"Published email for {cand['name']}."})
    if business.get("place_url"):
        routes.append({"route": "google_business_profile", "value": business["place_url"], "note": "Message/booking options may be available from the profile."})
    return routes


def find_for_business(
    business: dict,
    session: requests.Session,
    search_cache: dict,
    max_queries: int = 6,
    sleep_s: float = 1.2,
) -> dict:
    name = clean_text(business.get("name"))
    location = clean_text(business.get("location") or business.get("city"))
    website = clean_text(business.get("website"))
    candidates: list[dict] = []
    queries: list[str] = []
    limitations: list[str] = []

    pages = company_pages(session, website)
    for page in pages:
        try:
            candidates.extend(extract_from_html(page["html"], page["url"], name))
        except Exception as exc:
            limitations.append(f"Could not parse {page['url']}: {exc}")

    query_templates = [
        f'"{name}" {location} owner OR founder OR CEO',
        f'"{name}" {location} director OR manager',
        f'"{name}" "managing director" OR "practice manager"',
        f'site:linkedin.com/in "{name}" {location}',
        f'"{name}" {location} email contact',
        f'"{name}" {location} facebook about',
    ]
    if website:
        query_templates.append(f'site:{domain_of(website)} team OR leadership OR founder')

    for template in query_templates[:max_queries]:
        queries.append(template)
        results = search_web(session, template, search_cache)
        candidates.extend(candidates_from_search(results, name, location))
        time.sleep(sleep_s)

    candidates = dedupe_candidates(candidates)

    # If a real owner/executive is present, keep only the most relevant clinical
    # names; a clinic page can list 20 doctors but the buyer is the principal.
    if any(c.get("seniority") in ("owner", "executive") for c in candidates):
        keep = []
        clinical_kept = 0
        for cand in candidates:
            if cand.get("seniority") != "clinical":
                keep.append(cand)
            elif clinical_kept < 2:
                keep.append(cand)
                clinical_kept += 1
        candidates = keep[:12]
    else:
        candidates = candidates[:12]

    for cand in candidates:
        email = clean_text(cand.get("email"))
        cand["email"] = email if email and EMAIL_RE.fullmatch(email) else ""
        cand["email_status"] = "published" if cand["email"] else "not_found"

    if not candidates:
        limitations.append(
            "No named decision maker was found in the company pages or public search results. "
            "Use the company page, contact form, or phone and ask for the owner/manager by role."
        )
    else:
        limitations.append(
            "Search snippets can be stale or refer to a different company with a similar name. "
            "Verify each person on the source URL before using their name or contact details."
        )

    search_urls = {
        "linkedin_people": f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(name + ' ' + location)}",
        "google": f"https://www.google.com/search?q={quote_plus(name + ' ' + location + ' owner OR founder')}",
        "facebook": f"https://www.facebook.com/search/top?q={quote_plus(name + ' ' + location)}",
        "website": website,
    }

    return {
        "business_name": name,
        "location": location,
        "website": website,
        "candidates": candidates,
        "contact_routes": contact_routes(business, candidates),
        "search_urls": search_urls,
        "searched_queries": queries,
        "pages_scanned": [p["url"] for p in pages],
        "limitations": limitations,
        "researched_at": utc_now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Find public decision makers for businesses.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="decision_makers.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-queries", type=int, default=6)
    parser.add_argument("--sleep", type=float, default=1.2)
    parser.add_argument("--cache", default=str(TMP_DIR / "lead_search_cache.json"))
    args = parser.parse_args()

    payload = load_json(args.input, [])
    businesses = payload.get("businesses", []) if isinstance(payload, dict) else payload
    if args.limit:
        businesses = businesses[: args.limit]
    if not businesses:
        raise SystemExit(f"No businesses found in {args.input}")

    cache = load_json(args.cache, {}) or {}
    session = requests.Session()
    results = []
    for i, business in enumerate(businesses, 1):
        print(f"[{i}/{len(businesses)}] {clean_text(business.get('name'))}")
        result = find_for_business(business, session, cache, args.max_queries, args.sleep)
        results.append(result)
        print(f"    {len(result['candidates'])} candidate(s), {len(result['searched_queries'])} query(ies)")

    save_json(args.cache, cache)
    save_json(args.output, {"generated_at": utc_now(), "results": results})
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()
