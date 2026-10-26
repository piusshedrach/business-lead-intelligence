#!/usr/bin/env python3
"""Merge discovery, audit, and decision-maker data into sales-ready deliverables.

Outputs:
  all_leads.csv
  website_opportunity_leads.csv
  site_findings.csv
  decision_makers.csv
  FINDINGS.md
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


def business_rows(businesses, audits, dms) -> tuple[list[dict], list[dict]]:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build lead deliverables from pipeline outputs.")
    parser.add_argument("--businesses", required=True)
    parser.add_argument("--audits", required=True)
    parser.add_argument("--decision-makers", default="")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--prefix", default="leads")
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

    rows, findings = business_rows(businesses, audits, dms)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_cols = [
        "business_name", "category", "location", "address", "phone", "email", "website",
        "website_type", "website_status", "website_status_label", "http_status",
        "audit_score", "grade", "opportunity_score", "priority", "qualification",
        "recommended_offer", "outreach_hook", "opening_line", "top_issues", "strengths",
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
        "status_counts": dict(status_counts.most_common()),
    }
    save_json(out / f"{args.prefix}_run_summary.json", summary)
    save_json(out / f"{args.prefix}_all_leads.json", {"generated_at": utc_now(), "leads": rows})
    write_markdown(out / f"{args.prefix}_FINDINGS.md", businesses, audits, dms, rows, summary)

    print(f"Wrote deliverables to {out}/")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
