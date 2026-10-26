"""Shared helpers for the business lead intelligence pipeline."""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

PUA_RE = re.compile(r"[\ue000-\uf8ff]")
WS_RE = re.compile(r"\s+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().\-]{6,}\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

SOCIAL_HOSTS = {
    "facebook.com", "fb.com", "instagram.com", "linkedin.com", "youtube.com",
    "youtu.be", "tiktok.com", "twitter.com", "x.com", "pinterest.com",
    "threads.net", "snapchat.com", "wa.me", "whatsapp.com", "telegram.me",
    "t.me", "linktr.ee", "linkin.bio",
}
DIRECTORY_HOSTS = {
    "google.com", "goo.gl", "maps.google.com", "yelp.com", "yellowpages.com",
    "oladoc.com", "marham.pk", "sehat.com.pk", "pakistanyellowpages.com",
    "businesslist.pk", "clutch.co", "goodfirms.co", "trustpilot.com",
    "tripadvisor.com", "zomato.com", "foodpanda.pk", "daraz.pk",
    "bookme.pk", "bit.ly", "tinyurl.com", "blogspot.com", "wordpress.com",
    "webnode.com", "wixsite.com", "business.site", "godaddysites.com",
}
ROLE_WORDS = [
    "owner", "founder", "co-founder", "cofounder", "ceo", "chief executive",
    "managing director", "director", "proprietor", "principal", "partner",
    "president", "chairman", "chairperson", "chief operating officer", "coo",
    "chief marketing officer", "cmo", "marketing manager", "marketing head",
    "head of marketing", "general manager", "gm", "operations manager",
    "practice manager", "clinic manager", "office manager", "manager",
    "administrator", "business development manager", "sales manager",
    "head of sales", "hr manager", "it manager", "doctor", "dr.",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = PUA_RE.sub("", text)
    text = text.replace("\u200b", " ").replace("\xa0", " ")
    return WS_RE.sub(" ", text).strip(" \t\n\r|,-")


def clean_phone(value) -> str:
    text = unquote(clean_text(value))
    text = re.sub(r"^(?:tel|phone):\s*", "", text, flags=re.I)
    text = re.sub(r"[^\d+().\-\s]", "", text).strip()
    return WS_RE.sub(" ", text)


def normalize_phone(value) -> str:
    digits = re.sub(r"\D", "", clean_phone(value))
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def phones_match(left, right) -> bool:
    """True when two numbers are the same number in different formats.

    Handles +92 300 1234567 vs 0300-1234567 vs 0092..., and ignores short
    extension-like tails only when the significant digits still line up.
    """
    a = normalize_phone(left).lstrip("0")
    b = normalize_phone(right).lstrip("0")
    if not a or not b:
        return False
    if a == b:
        return True
    length = min(len(a), len(b), 10)
    return length >= 8 and a[-length:] == b[-length:]


def domain_of(url: str) -> str:
    if not url:
        return ""
    candidate = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
        candidate = "https://" + candidate
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return ""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def url_host_matches(url: str, hosts: set[str]) -> bool:
    host = domain_of(url)
    return any(host == h or host.endswith("." + h) for h in hosts)


def root_url(url: str) -> str:
    if not url:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc:
        return ""
    return urlunparse((p.scheme or "https", p.netloc, "/", "", "", ""))


def slugify(value: str, fallback: str = "lead") -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or fallback


def load_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str | Path, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def save_csv(path: str | Path, rows: list[dict], columns: list[str] | None = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_value(row.get(k)) for k in columns})


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def place_id_from_url(url: str) -> str:
    m = re.search(r"!1s([0-9a-fA-Fx]+:[0-9a-fA-Fx]+)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"place_id[:=]([A-Za-z0-9_\-]+)", url or "")
    return m.group(1) if m else ""


def dedupe_businesses(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        pid = clean_text(row.get("place_id")) or place_id_from_url(row.get("place_url", ""))
        name = clean_text(row.get("name")).lower()
        address = clean_text(row.get("address")).lower()
        phone = normalize_phone(row.get("phone", ""))
        key = pid or "|".join([name, address, phone])
        if not key or key in seen:
            continue
        seen.add(key)
        row["place_id"] = pid
        out.append(row)
    return out


def merge_business_rows(rows: list[dict]) -> list[dict]:
    """Merge duplicate discovery rows, preferring the row with more contact fields."""
    groups: dict[str, dict] = {}
    for row in rows:
        pid = clean_text(row.get("place_id")) or place_id_from_url(row.get("place_url", ""))
        key = pid or "|".join([
            clean_text(row.get("name")).lower(),
            clean_text(row.get("address")).lower(),
        ])
        if key not in groups:
            groups[key] = dict(row)
            groups[key]["place_id"] = pid
            continue
        current = groups[key]
        for field in ("phone", "website", "address", "category", "hours", "rating", "reviews", "place_url"):
            if not clean_text(current.get(field)) and clean_text(row.get(field)):
                current[field] = row.get(field)
    return list(groups.values())


def severity_points(severity: str) -> int:
    return {"critical": 25, "high": 10, "medium": 4, "low": 1}.get(severity, 2)
