#!/usr/bin/env python3
"""End-to-end orchestrator: Maps -> audit -> decision makers -> report -> emails.

Examples:
  # Full run (requires the Browser Control extension attached to a tab)
  python run_pipeline.py --business "dentists" --location "Lahore" \
      --areas "Gulberg, DHA, Johar Town" --limit 30 \
      --sender-name "Your Name" --sender-address "Your postal address"

  # Skip Maps when you already have a businesses JSON
  python run_pipeline.py --input data/businesses.json --business "dentists" \
      --location "Lahore" --skip-maps
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TMP_DIR = Path(tempfile.gettempdir()) / "opencode"
sys.path.insert(0, str(SCRIPT_DIR))

from audit_sites import audit_businesses  # noqa: E402
from common import (  # noqa: E402
    clean_text,
    dedupe_businesses,
    load_json,
    merge_business_rows,
    save_json,
    slugify,
    utc_now,
)
from find_decision_makers import find_for_business  # noqa: E402


def matches_pre_filters(business: dict, args) -> bool:
    """Rating / review / website filters applied before spending time on audits."""
    has_website = bool(clean_text(business.get("website")))
    if args.website == "has" and not has_website:
        return False
    if args.website == "none" and has_website:
        return False

    if args.min_rating is not None:
        try:
            rating = float(business.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        if rating < args.min_rating:
            return False

    if args.min_reviews is not None:
        raw = re.sub(r"\D", "", str(business.get("reviews") or "0")) or "0"
        if int(raw) < args.min_reviews:
            return False
    return True


def filter_by_audit(businesses: list[dict], audits: list[dict], args) -> tuple[list[dict], list[dict]]:
    """Score / priority filters applied once the website audit exists."""
    priorities = {p.strip().lower() for p in (args.priority or "").split(",") if p.strip()}
    if args.min_score is None and args.max_score is None and not priorities:
        return businesses, audits

    audit_by = {clean_text(a.get("business_name")).lower(): a for a in audits}
    keep_names: set[str] = set()
    for business in businesses:
        name = clean_text(business.get("name")).lower()
        audit = audit_by.get(name)
        if not audit:
            continue
        score = audit.get("score")
        if args.min_score is not None:
            if not isinstance(score, (int, float)) or score < args.min_score:
                continue
        if args.max_score is not None:
            if not isinstance(score, (int, float)) or score > args.max_score:
                continue
        if priorities and audit.get("priority") not in priorities:
            continue
        keep_names.add(name)

    return (
        [b for b in businesses if clean_text(b.get("name")).lower() in keep_names],
        [a for a in audits if clean_text(a.get("business_name")).lower() in keep_names],
    )


def build_queries(terms: list[str], location: str, areas: list[str], cap: int = 12) -> list[str]:
    queries: list[str] = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        queries.extend(
            [
                f"{term} in {location}",
                f"best {term} in {location}",
                f"{term} near {location}",
                f"top rated {term} {location}",
            ]
        )
        for area in areas:
            area = area.strip()
            if area:
                queries.append(f"{term} in {area} {location}")
    seen = set()
    out = []
    for q in queries:
        q = " ".join(q.split())
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:cap]


def run_maps(queries: list[str], limit_per_query: int, output: Path, max_scrolls: int, driver_mode: str = "auto") -> dict:
    cfg_path = TMP_DIR / "maps-config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "queries": queries,
        "limit_per_query": limit_per_query,
        "output": str(output.resolve()),
        "max_scrolls": max_scrolls,
    }
    save_json(cfg_path, cfg)
    print(f"[pipeline] Maps config -> {cfg_path}")
    print(f"[pipeline] Queries: {len(queries)}; limit/query: {limit_per_query}")
    browser_error = ""
    if driver_mode in ("browser", "auto"):
        script = SCRIPT_DIR / "maps_extract.js"
        cmd = ["browser-control", "execute", "--file", str(script), "--json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            if proc.returncode == 0 and output.exists():
                return load_json(output, {})
            browser_error = (proc.stderr or proc.stdout or "unknown error")[-2500:]
        except FileNotFoundError:
            browser_error = "browser-control is not on PATH."
        except subprocess.TimeoutExpired:
            browser_error = "Maps extraction timed out after 2 hours."

        if driver_mode == "browser":
            raise SystemExit(
                "Maps extraction with Browser Control failed. Open a tab in your browser, "
                "click the Browser Control toolbar button, then re-run. Details:\n" + browser_error
            )
        print("[pipeline] Browser Control unavailable; falling back to headless Firefox.")
        if browser_error:
            print("[pipeline] Browser Control detail: " + browser_error.splitlines()[-1][:300])

    if driver_mode in ("selenium", "auto"):
        script = SCRIPT_DIR / "maps_extract_selenium.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--config", str(cfg_path)],
            capture_output=True,
            text=True,
            timeout=7200,
        )
        if proc.returncode != 0 or not output.exists():
            raise SystemExit(
                "Maps extraction failed with both Browser Control and headless Firefox.\n"
                + (proc.stderr or proc.stdout)[-3000:]
                + ("\nBrowser Control: " + browser_error if browser_error else "")
            )
        return load_json(output, {})

    raise SystemExit(f"Unknown driver mode: {driver_mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end local business lead intelligence pipeline.")
    parser.add_argument("--business", help='Niche, e.g. "dentists" or "gyms".')
    parser.add_argument("--location", help='City/region, e.g. "Lahore".')
    parser.add_argument("--areas", default="", help="Comma-separated sub-localities to widen coverage.")
    parser.add_argument("--input", default="", help="Prepared businesses JSON (skips Maps when used with --skip-maps).")
    parser.add_argument("--skip-maps", action="store_true")
    parser.add_argument("--limit", type=int, default=30, help="Maximum unique businesses to keep.")
    parser.add_argument("--min-rating", type=float, default=None, help="Keep businesses at or above this Google rating.")
    parser.add_argument("--min-reviews", type=int, default=None, help="Keep businesses with at least this many Google reviews.")
    parser.add_argument("--website", choices=["any", "has", "none"], default="any",
                        help="Filter by whether Google Maps lists a website.")
    parser.add_argument("--min-score", type=float, default=None, help="Keep audits at or above this score.")
    parser.add_argument("--max-score", type=float, default=None, help="Keep audits at or below this score.")
    parser.add_argument("--priority", default="", help="Comma-separated priorities to keep: critical,high,medium,low.")
    parser.add_argument("--limit-per-query", type=int, default=20)
    parser.add_argument("--max-scrolls", type=int, default=8)
    parser.add_argument("--driver", choices=["auto", "browser", "selenium"], default="auto",
                        help="Maps driver. auto tries Browser Control, then headless Firefox.")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--no-link-check", action="store_true")
    parser.add_argument("--skip-decisions", action="store_true")
    parser.add_argument("--max-queries", type=int, default=6, help="Decision-maker search queries per business.")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--sender-name", default="[Your name]", help="Name used in cold-email signatures.")
    parser.add_argument("--sender-company", default="", help="Company used in cold-email signatures.")
    parser.add_argument("--sender-address", default="[Your postal address]", help="Postal address required in cold emails.")
    args = parser.parse_args()

    if not args.business and not args.input:
        raise SystemExit("Provide --business and --location, or --input with --skip-maps.")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or (
        f"{slugify(args.location or 'leads')}_{slugify(args.business or 'business')}"
    )
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    if args.skip_maps:
        if not args.input:
            raise SystemExit("--skip-maps requires --input.")
        payload = load_json(args.input, {})
        businesses = payload.get("businesses", []) if isinstance(payload, dict) else payload
    else:
        if not args.location:
            raise SystemExit("--location is required for Maps discovery.")
        terms = [t.strip() for t in (args.business or "").split(",") if t.strip()]
        areas = [a.strip() for a in args.areas.split(",") if a.strip()]
        queries = build_queries(terms, args.location, areas)
        maps_out = data_dir / f"{prefix}_businesses_raw.json"
        payload = run_maps(queries, args.limit_per_query, maps_out, args.max_scrolls, args.driver)
        businesses = payload.get("businesses", [])

    businesses = merge_business_rows(businesses)
    businesses = dedupe_businesses(businesses)
    businesses = [b for b in businesses if matches_pre_filters(b, args)]
    businesses = businesses[: args.limit]

    if not businesses:
        raise SystemExit(
            "No businesses matched the filters. Loosen --min-rating/--min-reviews/--website "
            "or widen the Maps query."
        )
    for business in businesses:
        business.setdefault("location", args.location or "")
        business.setdefault("city", args.location or "")

    if not businesses:
        raise SystemExit("No businesses were extracted. Check the Maps query, network, and consent screen.")

    businesses_path = data_dir / f"{prefix}_businesses.json"
    save_json(
        businesses_path,
        {
            "generated_at": utc_now(),
            "business": args.business,
            "location": args.location,
            "count": len(businesses),
            "businesses": businesses,
        },
    )
    print(f"[pipeline] Normalized {len(businesses)} unique businesses -> {businesses_path}")

    print(f"[pipeline] Auditing websites ({args.workers} workers)...")
    audits = audit_businesses(
        businesses,
        workers=args.workers,
        timeout=args.timeout,
        check_links=not args.no_link_check,
    )
    audits_path = data_dir / f"{prefix}_audits.json"
    save_json(audits_path, {"generated_at": utc_now(), "audits": audits})
    print(f"[pipeline] Audits -> {audits_path}")

    businesses, audits = filter_by_audit(businesses, audits, args)
    if not businesses:
        raise SystemExit(
            "No audits matched --min-score/--max-score/--priority. Relax the filter and re-run."
        )
    save_json(
        businesses_path,
        {
            "generated_at": utc_now(),
            "business": args.business,
            "location": args.location,
            "count": len(businesses),
            "businesses": businesses,
        },
    )

    dm_path = ""
    if not args.skip_decisions:
        print("[pipeline] Researching public decision makers...")
        import requests

        session = requests.Session()
        cache_path = TMP_DIR / "lead_search_cache.json"
        cache = load_json(cache_path, {}) or {}
        results = []
        for i, business in enumerate(businesses, 1):
            print(f"  [{i}/{len(businesses)}] {business.get('name', '')}")
            results.append(
                find_for_business(
                    business,
                    session,
                    cache,
                    max_queries=args.max_queries,
                    sleep_s=1.2,
                )
            )
        save_json(cache_path, cache)
        dm_path = str(data_dir / f"{prefix}_decision_makers.json")
        save_json(dm_path, {"generated_at": utc_now(), "results": results})
        print(f"[pipeline] Decision makers -> {dm_path}")

    print("[pipeline] Building deliverables...")
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "build_report.py"),
        "--businesses",
        str(businesses_path),
        "--audits",
        str(audits_path),
        "--output-dir",
        str(out_dir),
        "--prefix",
        prefix,
        "--sender-name",
        args.sender_name,
        "--sender-company",
        args.sender_company,
        "--sender-address",
        args.sender_address,
    ]
    if dm_path:
        cmd.extend(["--decision-makers", dm_path])
    env = {**os.environ, "PYTHONPATH": str(SCRIPT_DIR)}
    proc = subprocess.run(cmd, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit("Report build failed.")

    print("")
    print(f"[pipeline] Done. Open {out_dir}/{prefix}_FINDINGS.md")
    print(f"[pipeline] Leads: {out_dir}/{prefix}_all_leads.csv")
    print(f"[pipeline] Opportunities: {out_dir}/{prefix}_website_opportunity_leads.csv")
    print(f"[pipeline] Cold email drafts: {out_dir}/{prefix}_cold_emails.md")


if __name__ == "__main__":
    main()
