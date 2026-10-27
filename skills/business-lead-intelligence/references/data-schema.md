# Data schema

## `businesses.json`

```json
{
  "generated_at": "2026-10-26T09:00:00+00:00",
  "business": "dentists",
  "location": "Lahore",
  "count": 24,
  "businesses": [
    {
      "name": "Smile Dental Clinic",
      "category": "Dental clinic",
      "address": "12 Main Boulevard, Gulberg III, Lahore",
      "phone": "+92 42 3571 1234",
      "website": "https://example.com",
      "rating": "4.8",
      "reviews": "212",
      "hours": "Monday–Saturday 10:00–20:00",
      "plus_code": "…",
      "place_url": "https://www.google.com/maps/place/...",
      "place_id": "0x...:0x...",
      "latitude": "31.52",
      "longitude": "74.35",
      "description": "Short text from the Maps panel",
      "query": "dentists in Lahore",
      "location": "Lahore",
      "city": "Lahore"
    }
  ]
}
```

Only `name` is required. Missing fields are written as empty strings, never
invented.

## `audits.json`

```json
{
  "generated_at": "...",
  "audits": [
    {
      "business_name": "Smile Dental Clinic",
      "website": "https://example.com",
      "website_url": "https://example.com",
      "website_type": "own",
      "website_status": "up",
      "website_status_detail": "",
      "http_status": 200,
      "final_url": "https://example.com/",
      "score": 58,
      "grade": "D",
      "opportunity_score": 42,
      "priority": "high",
      "findings": [
        {
          "id": "seo.title_missing",
          "category": "SEO",
          "severity": "high",
          "title": "Page title is missing",
          "evidence": "No <title> element was found.",
          "why": "…",
          "fix": "…",
          "effort": "low",
          "page": "https://example.com/"
        }
      ],
      "strengths": ["HTTPS is enabled"],
      "contact_channels": {
        "emails": ["info@example.com"],
        "phones": ["+92 42 3571 1234"],
        "whatsapp": ["https://wa.me/924235711234"],
        "social": {"Facebook": "https://facebook.com/example"},
        "forms": [{"action": "https://example.com/contact", "method": "POST", "inputs": 4, "has_email": true, "has_phone": true, "has_message": true}],
        "has_contact_form": true,
        "has_tel_link": true,
        "has_mailto": true
      },
      "tech": {"cms": "WordPress", "framework": "jQuery", "analytics": ["Google Analytics/GA4"]},
      "pages_checked": [{"label": "Contact", "url": "…", "status": 200, "title": "…", "h1": "…", "words": 210, "has_form": true}],
      "checks": {"http": {}, "tls": {}, "dns": {}, "page": {}, "links": {}},
      "limitations": ["…"],
      "verified_at": "..."
    }
  ]
}
```

`website_status` values:

- `no_website`
- `not_own_website`
- `up`
- `blocked_to_bots`
- `up_non_html`
- `down_http_4xx`
- `down_http_5xx`
- `down_ssl_error`
- `down_timeout`
- `down_dns_or_unreachable`
- `down_redirect_loop`
- `down_connection_reset`
- `down_other`
- `audit_error`

## `decision_makers.json`

```json
{
  "generated_at": "...",
  "results": [
    {
      "business_name": "Smile Dental Clinic",
      "location": "Lahore",
      "website": "https://example.com",
      "candidates": [
        {
          "name": "Dr. Ayesha Khan",
          "role": "Founder",
          "seniority": "owner",
          "source_type": "company_team_page",
          "source_url": "https://example.com/team",
          "evidence": "Dr. Ayesha Khan — Founder",
          "confidence": "high",
          "email": "ayesha@example.com",
          "email_status": "published",
          "linkedin": "https://www.linkedin.com/in/..."
        }
      ],
      "contact_routes": [
        {"route": "linkedin", "value": "https://www.linkedin.com/in/...", "note": "Dr. Ayesha Khan — Founder (verify profile before outreach)."}
      ],
      "search_urls": {
        "linkedin_people": "https://www.linkedin.com/search/results/people/?keywords=...",
        "google": "https://www.google.com/search?q=...",
        "facebook": "https://www.facebook.com/search/top?q=...",
        "website": "https://example.com"
      },
      "searched_queries": ["..."],
      "pages_scanned": ["https://example.com/about"],
      "limitations": ["..."],
      "researched_at": "..."
    }
  ]
}
```

`confidence`: `high` / `medium` / `low`.
`email_status`: `published` / `not_found`. Never output an inferred address as
`published`.

## Deliverable columns

### `*_all_leads.csv`

`business_name, category, location, address, phone, email, website,
website_type, website_status, website_status_label, http_status, audit_score,
grade, opportunity_score, priority, qualification, recommended_offer,
outreach_hook, opening_line, cold_email_subject, cold_email_body,
cold_email_status, email_notes, email_evidence, top_issues, strengths,
decision_makers, best_contact_route, rating, reviews, place_url, query,
verified_at`

`email` is the first published address found on the site (`mailto:`/visible
text). Google Maps does not expose email addresses, so it is empty when the
business publishes none anywhere the pipeline can read.

`outreach_hook` is the single strongest evidence-backed observable for the
first message. `opening_line` is a draft built only from that observable and
the verified status.

`cold_email_subject` and `cold_email_body` are a first-touch draft. The subject
is 2–4 lowercase words. The body contains one specific observation, one problem
or opportunity, one value statement, one low-pressure ask, the sender
signature, the postal address, and a natural opt-out. Replace any `[bracketed]`
sender placeholders before sending.

The automatic draft is a starting point. Rewrite it with the `cold-email` skill
and `references/email-copy.md` before sending. Do not claim a free landing page
exists unless it does. If you build one, keep the URL in a separate
`mockup_url` column or a mapping file, and send the link only after the prospect
asks for it.

`cold_email_status` is one of:

- `ready` — a published business email exists and a draft was generated.
- `no_published_email` — no published address was found. Use phone or the
  contact form; do not guess an address.
- `no_audit` — the audit was skipped, so no evidence-based draft exists.

`email_notes` explains whether the draft is addressed to a verified buyer or a
generic inbox. `email_evidence` is the exact finding used in the email body.
It may differ from `outreach_hook` because the email picks the most
customer-facing issue, not necessarily the strongest technical one.

The pipeline never sends email automatically. It does not verify inboxes or
manage deliverability. Sending is the optional `gmail_sender.py` step after
explicit approval. Use the `cold-email` skill for sending setup, warm-up,
cadence, and reply handling.

### `*_site_findings.csv`

`business_name, website, score, priority, category, severity, title, evidence,
why, fix, effort, page`

### `*_decision_makers.csv`

`business_name, name, role, seniority, confidence, email, email_status,
linkedin, source_type, source_url, evidence`

Here `email_status` is the candidate-level verification state (`published` /
`not_found`), not the cold-email readiness of the lead.

### `*_cold_emails.md`

One section per business, grouped into `Ready to send` and `No published email`.
Each section contains the recipient, cold-email status, phone, subject, full
plain-text body, review note, and the exact evidence used. The body includes the
sender signature and opt-out. Drafts marked `no_published_email` must not be
sent until a real address is found.

All CSVs are UTF-8 with BOM (`utf-8-sig`) for Excel compatibility.
