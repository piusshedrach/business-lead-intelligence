#!/usr/bin/env python3
"""Verify whether a business listed without a website actually has one.

Input is a JSON array. Each element is one business:

    [
      "SmileOn Dental Lahore",
      ["Dentocorrect", "Dentocorrect Gulberg", "Dentocorrect Dental Clinic"]
    ]

A plain string is one search query. A list of strings holds aliases, alternate
spellings, or former names of the *same* business; every alias is searched.

Each query goes to DuckDuckGo's HTML endpoint with Bing as the fallback. Results
from social networks, directories, search engines, and reference sites are not
treated as an owned website. A domain only becomes a candidate when the domain
name and the result text match the distinctive tokens of the business name.

Verdicts per business:

    has_website       A plausible owned domain was found. Verify it in a browser
                      before naming it in outreach.
    no_website_found  The engines returned results, but none was a plausible
                      owned domain (social-only results are reported).
    inconclusive      The engines could not be queried (blocked/rate-limited),
                      or returned nothing at all. Never report this as
                      "no website"; check manually.

Usage:
    python verify_website_presence.py --input queries.json \
        --output data/website_verification.json --city Lahore

    python verify_website_presence.py --input queries.json --csv out.csv

Stdlib plus requests and beautifulsoup4 (already required by this skill).
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from common import (
    SOCIAL_HOSTS,
    clean_text,
    configure_stdio,
    domain_of,
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

SEARCH_HOSTS = {
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "yandex.com",
    "yandex.ru", "baidu.com", "ecosia.org", "search.brave.com", "ask.com",
}
DIRECTORY_HOSTS = {
    "yelp.com", "yellowpages.com", "yellowpages.ca", "bbb.org", "oladoc.com",
    "marham.pk", "sehat.com.pk", "pakistanyellowpages.com", "businesslist.pk",
    "businesslist.com", "clutch.co", "goodfirms.co", "trustpilot.com",
    "tripadvisor.com", "zomato.com", "foodpanda.pk", "daraz.pk", "bookme.pk",
    "justdial.com", "practo.com", "healthgrades.com", "zocdoc.com",
    "glassdoor.com", "indeed.com", "cybo.com", "b2b.com", "opendi.com",
}
REFERENCE_HOSTS = {
    "wikipedia.org", "wikimedia.org", "wikidata.org", "crunchbase.com",
    "bloomberg.com", "forbes.com", "reuters.com", "bbc.com", "bbc.co.uk",
    "dawn.com", "tribune.com.pk", "thenews.com.pk", "nation.com.pk",
    "medium.com", "quora.com", "reddit.com", "youtube.com", "youtu.be",
}
FREE_HOSTS = {
    "blogspot.com", "wordpress.com", "wixsite.com", "business.site",
    "godaddysites.com", "webnode.com", "weebly.com", "square.site",
    "linktr.ee", "linkin.bio", "carrd.co", "sites.google.com",
}
TLD_PARTS = {
    "com", "net", "org", "biz", "info", "io", "co", "ng", "pk", "uk", "ae",
    "us", "ca", "au", "de", "fr", "in", "www",
}
STOPWORDS = {
    "the", "and", "for", "with", "from", "near", "best", "top", "official",
    "clinic", "clinics", "dental", "dentistry", "dentist", "dentists",
    "care", "center", "centre", "hospital", "hospitals", "medical", "health",
    "speech", "therapy", "therapist", "child", "children", "development",
    "school", "academy", "institute", "institution", "college", "university",
    "services", "service", "solutions", "solution", "company", "group",
    "associates", "association", "consulting", "consultants", "agency",
    "studio", "salon", "spa", "gym", "fitness", "hotel", "restaurant",
    "cafe", "shop", "store", "mart", "pvt", "private", "limited", "ltd",
    "llc", "inc", "plc", "co", "international", "global", "general",
    "specialist", "specialists", "specialty", "practice", "practices",
    "dr", "prof", "mr", "mrs", "ms",
}
CANDIDATE_MIN_SCORE = 45


def host_in(host: str, hosts: set[str]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def classify_host(url: str) -> str:
    host = domain_of(url)
    if not host:
        return "unknown"
    if host_in(host, SEARCH_HOSTS) or re.match(r"^(www\.)?google\.[a-z.]+$", host):
        return "search"
    if host_in(host, SOCIAL_HOSTS) or host_in(host, FREE_HOSTS):
        # linktr.ee is both social and a page builder; either way it is not an
        # independently owned domain.
        return "social" if host_in(host, SOCIAL_HOSTS) else "free_host"
    if host_in(host, DIRECTORY_HOSTS):
        return "directory"
    if host_in(host, REFERENCE_HOSTS):
        return "reference"
    return "own"


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


def search_duckduckgo(
    session: requests.Session, query: str, timeout: float, limit: int
) -> tuple[list[dict], str]:
    try:
        resp = session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={**HEADERS, "Referer": "https://html.duckduckgo.com/"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return [], f"request failed: {exc.__class__.__name__}"
    if resp.status_code == 202:
        return [], "blocked (HTTP 202 challenge)"
    if resp.status_code == 429:
        return [], "rate limited (HTTP 429)"
    if resp.status_code != 200:
        return [], f"HTTP {resp.status_code}"

    soup = BeautifulSoup(resp.text, "html.parser")
    out: list[dict] = []
    for result in soup.select("div.result"):
        link = result.select_one("a.result__a")
        if not link:
            continue
        href = link.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if not href.startswith("http"):
            continue
        snippet = result.select_one(".result__snippet")
        out.append(
            {
                "title": clean_text(link.get_text(" ", strip=True)),
                "snippet": clean_text(snippet.get_text(" ", strip=True)) if snippet else "",
                "url": href,
                "engine": "duckduckgo",
            }
        )
        if len(out) >= limit:
            break
    return out, ""


def search_bing(
    session: requests.Session, query: str, timeout: float, limit: int
) -> tuple[list[dict], str]:
    try:
        resp = session.get(
            "https://www.bing.com/search",
            params={"q": query, "count": max(limit, 10)},
            headers=HEADERS,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return [], f"request failed: {exc.__class__.__name__}"
    if resp.status_code == 429:
        return [], "rate limited (HTTP 429)"
    if resp.status_code != 200:
        return [], f"HTTP {resp.status_code}"

    soup = BeautifulSoup(resp.text, "html.parser")
    out: list[dict] = []
    for li in soup.select("li.b_algo"):
        link = li.select_one("h2 a")
        if not link:
            continue
        href = decode_bing_url(link.get("href", ""))
        if not href.startswith("http"):
            continue
        snippet = li.select_one(".b_caption p") or li.select_one("p")
        out.append(
            {
                "title": clean_text(link.get_text(" ", strip=True)),
                "snippet": clean_text(snippet.get_text(" ", strip=True)) if snippet else "",
                "url": href,
                "engine": "bing",
            }
        )
        if len(out) >= limit:
            break
    return out, ""


def result_is_relevant(result: dict, required_tokens: list[str]) -> bool:
    """True when a result actually mentions the business, not just the city.

    Search engines sometimes serve an unrelated SERP to bots. Those results
    must not be counted as a successful lookup, or they would turn into a
    false "no website found" verdict.
    """
    if not required_tokens:
        return True
    blob = " ".join(
        [result.get("title", ""), result.get("snippet", ""), result.get("url", "")]
    ).lower()
    return any(token in blob for token in required_tokens)


def search_query(
    session: requests.Session,
    query: str,
    engine: str,
    timeout: float,
    delay: float,
    limit: int,
    required_tokens: list[str] | None = None,
) -> dict:
    """Search one query and report which engine answered and what failed."""
    record = {"query": query, "ok": False, "results": [], "errors": [], "engines": []}
    required_tokens = required_tokens or []

    def run(name: str) -> tuple[list[dict], str]:
        if name == "duckduckgo":
            found, error = search_duckduckgo(session, query, timeout, limit)
        else:
            found, error = search_bing(session, query, timeout, limit)
        time.sleep(delay)
        record["engines"].append(name)
        if found and required_tokens:
            found = [item for item in found if result_is_relevant(item, required_tokens)]
        if not found and not error:
            # An empty or irrelevant SERP is indistinguishable from a soft
            # block; never let it become evidence that there is no website.
            error = "no relevant results (possible soft block or namesake results)"
        if error:
            record["errors"].append(f"{name}: {error}")
        else:
            record["ok"] = True
        return found, error

    if engine in ("duckduckgo", "auto"):
        found, _ = run("duckduckgo")
        record["results"] = found
        if not found and engine == "auto":
            found, _ = run("bing")
            record["results"] = found
    elif engine == "bing":
        found, _ = run("bing")
        record["results"] = found
    else:  # both
        first, _ = run("duckduckgo")
        second, _ = run("bing")
        record["results"] = first + second

    seen: set[str] = set()
    deduped = []
    for item in record["results"]:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    record["results"] = deduped[: limit * 2]
    return record


def name_tokens(aliases: list[str]) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+", " ".join(aliases).lower()):
        if len(token) < 3 or token in STOPWORDS or token.isdigit():
            continue
        if token not in tokens:
            tokens.append(token)
    if not tokens:  # every word was generic; fall back to all words
        for token in re.findall(r"[a-z0-9]+", " ".join(aliases).lower()):
            if len(token) >= 3 and token not in tokens:
                tokens.append(token)
    return tokens


def domain_match(tokens: list[str], url: str) -> float:
    host = domain_of(url)
    parts = [
        part
        for part in re.split(r"[^a-z0-9]+", host.lower())
        if part and part not in TLD_PARTS
    ]
    joined = "".join(parts)
    if not tokens or not joined:
        return 0.0
    hits = 0
    for token in tokens:
        if token in joined:
            hits += 1
            continue
        if any(len(token) >= 4 and (token in part or part in token) for part in parts):
            hits += 1
    return hits / len(tokens)


def text_match(tokens: list[str], title: str, snippet: str) -> float:
    blob = f"{title} {snippet}".lower()
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if token in blob) / len(tokens)


def score_result(tokens: list[str], result: dict) -> dict:
    dm = domain_match(tokens, result["url"])
    tm = text_match(tokens, result.get("title", ""), result.get("snippet", ""))
    score = round(100 * (0.6 * dm + 0.4 * tm))
    return {
        "domain": domain_of(result["url"]),
        "url": result["url"],
        "title": result.get("title", ""),
        "snippet": result.get("snippet", ""),
        "query": result.get("query", ""),
        "engine": result.get("engine", ""),
        "kind": classify_host(result["url"]),
        "score": score,
    }


def load_input(path: str) -> list[dict]:
    if path == "-":
        raw = sys.stdin.read()
        source = "<stdin>"
    else:
        source = path
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"[fail] Input file not found: {path}")
        raw = p.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[fail] {source} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit(
            "[fail] Input must be a JSON array. Each element is a string "
            "(one query) or an array of strings (aliases of one business)."
        )

    items: list[dict] = []
    for index, entry in enumerate(data):
        if isinstance(entry, str):
            aliases = [entry]
        elif isinstance(entry, list) and all(isinstance(x, str) for x in entry):
            aliases = entry
        else:
            raise SystemExit(
                f"[fail] Element {index} must be a string or an array of strings; "
                f"got {type(entry).__name__}."
            )
        cleaned: list[str] = []
        for alias in aliases:
            alias = clean_text(alias)
            if alias and alias not in cleaned:
                cleaned.append(alias)
        if not cleaned:
            raise SystemExit(f"[fail] Element {index} has no non-empty query strings.")
        items.append({"index": index, "aliases": cleaned})
    return items


def manual_url(aliases: list[str], city: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(" ".join(aliases + ([city] if city else [])))


def build_item(
    entry: dict,
    session: requests.Session,
    args,
    cache: dict[str, dict],
) -> dict:
    aliases = entry["aliases"]
    tokens = name_tokens(aliases)
    city_tokens = set(re.findall(r"[a-z0-9]+", args.city.lower()))
    required_tokens = [token for token in tokens if token not in city_tokens] or tokens
    limit = args.max_results

    searches: list[dict] = []
    effective_queries: list[str] = []
    all_results: list[dict] = []
    for alias in aliases:
        query = alias
        if args.city and args.city.lower() not in alias.lower():
            query = f"{alias} {args.city}"
        effective_queries.append(query)
        if query not in cache:
            cache[query] = search_query(
                session, query, args.engine, args.timeout, args.delay, limit,
                required_tokens,
            )
        record = cache[query]
        searches.append(
            {
                "query": query,
                "engines": record["engines"],
                "ok": record["ok"],
                "errors": record["errors"],
                "result_count": len(record["results"]),
            }
        )
        for result in record["results"]:
            all_results.append({**result, "query": query})

    unique: dict[str, dict] = {}
    for result in all_results:
        unique.setdefault(result["url"], result)

    scored = [score_result(tokens, result) for result in unique.values()]
    scored.sort(key=lambda row: row["score"], reverse=True)

    candidates = [
        row
        for row in scored
        if row["kind"] in ("own", "free_host")
        and row["kind"] != "unknown"
        and domain_match(tokens, row["url"]) > 0
        and row["score"] >= CANDIDATE_MIN_SCORE
    ]
    best_by_domain: dict[str, dict] = {}
    for row in candidates:
        domain = row["domain"]
        if domain not in best_by_domain or row["score"] > best_by_domain[domain]["score"]:
            best_by_domain[domain] = {**row, "query": row.get("query", "")}
    candidate_sites = sorted(
        best_by_domain.values(), key=lambda row: row["score"], reverse=True
    )[:5]

    socials: dict[str, dict] = {}
    for row in scored:
        if row["kind"] == "social":
            socials.setdefault(row["domain"], row)
    social_profiles = list(socials.values())[:5]

    results = [
        {
            "title": row["title"],
            "url": row["url"],
            "snippet": row["snippet"],
            "domain": row["domain"],
            "kind": row["kind"],
            "query": row["query"],
            "engine": row["engine"],
        }
        for row in scored
    ]

    limitations: list[str] = []
    for search in searches:
        for error in search["errors"]:
            limitations.append(f'Search "{search["query"]}" {error}.')
    if not any(search["ok"] for search in searches):
        verdict = "inconclusive"
        confidence = "low"
        limitations.append(
            "No search engine could be queried; do not treat this as evidence "
            "that the business has no website."
        )
    elif candidate_sites:
        verdict = "has_website"
        confidence = "high" if candidate_sites[0]["score"] >= 70 else "medium"
        limitations.append(
            "Search results are evidence, not proof: open the candidate URL and "
            "confirm it belongs to this business before using it in outreach."
        )
    else:
        verdict = "no_website_found"
        if not scored:
            confidence = "low"
            limitations.append(
                "The engines returned no results at all; the name may be too "
                "generic or the request throttled. Check manually."
            )
        else:
            confidence = "high" if len(scored) >= 3 else "medium"
            limitations.append(
                "No owned domain appeared in the top results. Social or directory "
                "presence alone is not a website."
            )

    return {
        "index": entry["index"],
        "aliases": aliases,
        "searched_queries": effective_queries,
        "verdict": verdict,
        "confidence": confidence,
        "candidate_sites": candidate_sites,
        "social_profiles": social_profiles,
        "results": results,
        "search_engines": sorted({e for s in searches for e in s["engines"]}),
        "searches": searches,
        "limitations": limitations,
        "manual_check_url": manual_url(aliases, args.city),
        "verified_at": utc_now(),
    }


def write_csv(path: str, items: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "index", "aliases", "verdict", "confidence", "candidate_domains",
        "candidate_urls", "social_domains", "result_count", "manual_check_url",
        "verified_at",
    ]
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "index": item["index"],
                    "aliases": " | ".join(item["aliases"]),
                    "verdict": item["verdict"],
                    "confidence": item["confidence"],
                    "candidate_domains": " | ".join(
                        c["domain"] for c in item["candidate_sites"]
                    ),
                    "candidate_urls": " | ".join(
                        c["url"] for c in item["candidate_sites"]
                    ),
                    "social_domains": " | ".join(
                        s["domain"] for s in item["social_profiles"]
                    ),
                    "result_count": len(item["results"]),
                    "manual_check_url": item["manual_check_url"],
                    "verified_at": item["verified_at"],
                }
            )


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Verify whether businesses listed without a website actually have one, "
            "by searching DuckDuckGo/Bing for their name and aliases."
        )
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help='JSON array of queries, or "-" to read stdin.',
    )
    parser.add_argument(
        "--output", "-o", default="",
        help="Write the full JSON report here (directories are created).",
    )
    parser.add_argument(
        "--csv", default="",
        help="Optional CSV summary path (one row per business).",
    )
    parser.add_argument(
        "--city", default="",
        help="Append this city/region to every query unless already present.",
    )
    parser.add_argument(
        "--engine", default="auto",
        choices=["auto", "duckduckgo", "bing", "both"],
        help="auto = DuckDuckGo, then Bing when DDG returns nothing (default).",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout.")
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Seconds to sleep after each network request (politeness).",
    )
    parser.add_argument(
        "--max-results", type=int, default=8,
        help="Results to keep per query per engine (default 8).",
    )
    args = parser.parse_args()

    items = load_input(args.input)
    session = requests.Session()
    session.headers.update(HEADERS)
    cache: dict[str, dict] = {}
    report_items = []
    for n, entry in enumerate(items, 1):
        item = build_item(entry, session, args, cache)
        report_items.append(item)
        names = " / ".join(item["aliases"])
        if len(names) > 70:
            names = names[:67] + "..."
        domains = ", ".join(c["domain"] for c in item["candidate_sites"][:2])
        suffix = f" [{domains}]" if domains else ""
        print(
            f"[{n}/{len(items)}] {names} -> {item['verdict']} "
            f"({item['confidence']}){suffix}",
            flush=True,
        )

    summary = {
        "total": len(report_items),
        "has_website": sum(1 for i in report_items if i["verdict"] == "has_website"),
        "no_website_found": sum(
            1 for i in report_items if i["verdict"] == "no_website_found"
        ),
        "inconclusive": sum(
            1 for i in report_items if i["verdict"] == "inconclusive"
        ),
    }
    report = {
        "generated_at": utc_now(),
        "input": args.input,
        "city": args.city,
        "engine": args.engine,
        "summary": summary,
        "items": report_items,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] JSON report: {out}")
    if args.csv:
        write_csv(args.csv, report_items)
        print(f"[ok] CSV summary: {args.csv}")

    print(
        "Summary: {total} checked, {has_website} has_website, "
        "{no_website_found} no_website_found, {inconclusive} inconclusive.".format(
            **summary
        )
    )
    if not args.output and not args.csv:
        print("Tip: pass --output to save the full evidence report.")


if __name__ == "__main__":
    main()
