---
name: business-lead-intelligence
description: >
  End-to-end local business lead intelligence: browse Google Maps for a
  user-provided niche, location, and criteria (rating, review count, website
  presence, site quality), extract name, phone, published email, address,
  rating, and website, qualify each business, audit whether the website works
  and what is missing/broken and how to fix it, write a detailed finding per
  business, research named decision makers beyond the listed phone, and generate
  an evidence-based outreach hook and opening line for proposals. Use when asked
  to find leads, audit local business websites, build a prospect list, filter
  businesses by rating or website status, or find owners/founders/managers for
  outreach.
---

# Business Lead Intelligence

A deterministic, evidence-first pipeline for local business prospecting. It
combines Browser Control (the user's real browser) for Google Maps with plain
HTTP/HTML auditing and public-source decision-maker research.

**Core rule: never fabricate a business, phone number, email, person, score, or
finding.** Every claim must come from a page that was actually fetched or a
search result that is cited. If something cannot be verified, label it
`not_found` / `unverified` and say what to check manually.

---

## What this skill produces

| File | Purpose |
|---|---|
| `output/<prefix>_all_leads.csv` | Every unique business with contact (including published email), site status, score, priority, offer, outreach hook, opening line |
| `output/<prefix>_website_opportunity_leads.csv` | Businesses with no site, a broken site, or a weak site |
| `output/<prefix>_site_findings.csv` | One row per finding: severity, evidence, why, fix, page |
| `output/<prefix>_decision_makers.csv` | Named people, role, confidence, source URL, published email |
| `output/<prefix>_FINDINGS.md` | Detailed per-business report with prioritized fixes and outreach routes |
| `output/<prefix>_run_summary.json` | Counts and averages used in the summary |

---

## Phase 0 — Confirm inputs and setup

Required from the user:
- **Business niche** (e.g. `dentists`, `gyms`, `software houses`)
- **Location** (city/region, e.g. `Lahore, Pakistan`)
- Optional: sub-localities, a lead cap, an existing businesses JSON
- Optional filters: minimum Google rating, minimum review count, website
  presence (`has` / `none`), minimum/maximum audit score, priority
  (`critical,high`), or "only weak sites" (`--max-score 50`)

If the user says "only businesses with no website", "rating above 4.5", "at
least 50 reviews", or "only broken/weak sites", map that to the filters below
instead of filtering manually after the run.

Run from a writable workspace (the pipeline writes `data/` and `output/` to
the current directory):

```bash
# Point this at wherever the skill is installed.
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"

cd ~/lead-runs
python3 -m venv .venv
.venv/bin/pip install -r "$SKILL_DIR/requirements.txt"
export PY="$PWD/.venv/bin/python"
"$PY" "$SKILL_DIR/scripts/doctor.py"
```

On Windows use `.venv\Scripts\python` and `.venv\Scripts\pip`. If you prefer an
existing Python environment, install `requirements.txt` there and use that
interpreter instead. Python 3.10+ is required.

All command examples below use `$PY` for the interpreter and `$SKILL_DIR` for
the installed skill directory. They are intentionally not tied to one machine.

Browser Control must be attached to a real tab:
1. Open any tab in the user's Chromium-family browser.
2. Click the **Browser Control** toolbar button on that tab.
3. Keep the tab open for the run.

If the extension is not attached, the first `browser-control execute` starts the
relay and waits up to ~35 seconds. Ask the user to attach it; do not bypass
CAPTCHA, consent, or login walls.

The orchestrator supports three Maps drivers:

```bash
--driver auto       # try Browser Control, then headless Firefox (default)
--driver browser    # Browser Control only
--driver selenium   # headless Firefox + Selenium only
```

`maps_extract_selenium.py` uses the system Firefox and the same config file. It
is a fallback, not an anti-bot tool: handle the consent screen only. If Google
blocks the headless session or returns zero rows, use Browser Control with a
real tab and inspect the page before changing selectors.

---

## Phase 1 — Plan the query matrix

Normalize the niche into 2–3 exact search terms (avoid generic drift):

| Niche | Terms |
|---|---|
| dentists | dentist, dental clinic, dental surgeon |
| gyms | gym, fitness center, fitness studio |
| software houses | software house, software company, IT company |
| physiotherapy | physiotherapist, physiotherapy clinic, physical therapy |

Build a balanced matrix:
- `{term} in {location}`
- `best {term} in {location}`
- `{term} near {location}`
- `{term} in {sub-locality} {location}` for each area the user supplies

Keep the total to ~8–12 queries per run. More queries increase coverage but
also duplicates and runtime.

---

## Phase 2 — Discover on Google Maps

```bash
$PY "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "dentists" \
  --location "Lahore" \
  --areas "Gulberg, DHA, Johar Town" \
  --limit 30 \
  --max-queries 6
```

Narrow the list to what the user actually wants:

```bash
# Only businesses with no website (the strongest new-site opportunity)
$PY "$SKILL_DIR/scripts/run_pipeline.py" ... \
  --website none --min-rating 4.0 --min-reviews 20

# Only weak live sites worth a rebuild/rescue pitch
$PY "$SKILL_DIR/scripts/run_pipeline.py" ... \
  --website has --max-score 50

# Only the hottest audits
$PY "$SKILL_DIR/scripts/run_pipeline.py" ... --priority critical,high

# Only established businesses
$PY "$SKILL_DIR/scripts/run_pipeline.py" ... \
  --min-rating 4.5 --min-reviews 100
```

Filters run in two stages: rating/reviews/website before audits (cheap), and
score/priority after audits (accurate). Because filters can remove rows, the
final count may be below `--limit`; widen the query matrix or raise
`--limit-per-query` if you need more.

The orchestrator writes `maps-config.json` in the system temp directory
(`/tmp/opencode` on most Linux/macOS systems), then calls
`browser-control execute --file maps_extract.js`. The extractor:

1. Opens `google.com/maps/search/{query}`.
2. Dismisses a consent screen if present.
3. Scrolls the results feed until the limit or a stable end.
4. Opens each place detail URL.
5. Extracts name, category, address, phone, website, rating, reviews, hours,
   plus code, place URL/ID, and coordinates.
6. Deduplicates by place ID, then by normalized name + address.
7. Writes `data/<prefix>_businesses_raw.json`.

If Maps changes its DOM, inspect one place page with Browser Control
(`snapshot()`) and update the selectors in `maps_extract.js`. Do not guess:
read the actual page first.

**Prepared input:** if the user already has a JSON export, skip Maps:

```bash
$PY "$SKILL_DIR/scripts/run_pipeline.py" \
  --input data/businesses.json --business "dentists" --location "Lahore" \
  --skip-maps --limit 30
```

Accepted input: a JSON list, or `{"businesses": [...]}`. Each business should
have at least `name`; ideally `website`, `phone`, `address`, `place_url`.

---

## Phase 3 — Qualify the website

The auditor classifies every listing:

| Type | Meaning | Default status |
|---|---|---|
| `none` | No website field | `no_website` |
| `social` | Facebook/Instagram/LinkedIn/WhatsApp/link-in-bio only | `not_own_website` |
| `directory` | Oladoc, Marham, Yelp, Google, page builder, shortlink | `not_own_website` |
| `own` | A real domain | `up`, `blocked_to_bots`, or `down_*` |

For owned sites it checks:

- **Availability:** DNS, TCP, TLS handshake, HTTP status, redirect chain, TTFB
- **Security:** HTTPS, certificate expiry, mixed content, HSTS, CSP, frame
  protection, server version disclosure, insecure form action
- **SEO:** title, meta description, H1, canonical, robots/noindex, lang,
  sitemap/robots, Open Graph, favicon, LocalBusiness JSON-LD and required
  fields, location relevance, thin content, broken links, alt coverage
- **Mobile:** viewport presence and responsiveness
- **Conversion/trust:** click-to-call, contact form/email, form fields, CTA,
  reviews/testimonials, address/map, hours, privacy policy, stale copyright,
  WhatsApp, contact page
- **NAP consistency:** listed phone and address vs. website
- **Performance:** response time, HTML size, request count, compression,
  lazy-loading, cache headers
- **Tech stack:** CMS/framework, analytics, chat/booking tags
- **Pages:** samples Contact, About, Team, Services, Booking, Pricing, Blog
  and records status/title/H1/word count/form presence

Scores are a **deduction model** (critical 25, high 12, medium 6, low 2) capped
at 0–100. The score is a prioritization aid, not a guarantee of quality. The
detailed findings and evidence are the real deliverable.

**Important distinction:** HTTP 403/429 is reported as `blocked_to_bots`, not
"down". A bot block may not affect real visitors. Verify those in a browser.

Read `references/audit-rules.md` for the full rule set and recommended fixes.

---

## Phase 4 — Write detailed findings

`build_report.py` writes one section per business in `FINDINGS.md`:

- Snapshot: score, grade, priority, qualification, offer, NAP, rating, email
- Contact channels
- **Outreach angle:** the strongest observable, the recommended angle, and a
  draft opening line built only from verified findings
- What works
- Prioritized findings, each with **Where / Evidence / Why it matters / How to
  fix / Effort**
- Decision makers and all contact routes
- Evidence, sources, and limitations

When presenting results, do not just dump the CSV. Summarize:
1. How many businesses, how many opportunities, how many hot/warm.
2. The 5–10 highest-opportunity businesses and the exact reason each is a lead.
3. The most common missing/broken patterns across the set (e.g. "12 of 20 have
   no mobile viewport; 9 have no LocalBusiness schema").
4. The best outreach angle per hot lead.
5. Data-quality caveats (blocked sites, missing phones, ambiguous names).

---

## Phase 5 — Find decision makers

The researcher uses only public, lawful sources:

1. **The business's own site:** team/about/leadership/management/staff/doctors
   pages, JSON-LD `Person`, published `mailto:` addresses, LinkedIn links.
2. **Public search results:** DuckDuckGo HTML (Bing fallback) queries such as
   `"Business" "Location" owner OR founder OR CEO`,
   `site:linkedin.com/in "Business"`, `"Business" email`.
3. **Public profiles exposed by the business:** Facebook, Instagram, LinkedIn
   company page, Google Business Profile.

It does **not** log into LinkedIn, scrape profiles behind auth, or bypass
access controls. It does not guess emails. A candidate is only emitted with:
- a name,
- a role,
- a source URL,
- an evidence snippet,
- a confidence (`high` / `medium` / `low`).

Prioritize roles in this order: Owner / Founder / Co-Founder → CEO / Managing
Director / Director → Practice/Clinic/General Manager → Marketing Manager →
Operations/Office Manager. A clinician only counts as a decision maker when the
source also shows an ownership/leadership role (owner, principal, director).
Rank-and-file dentists are reported as clinical contacts, not as the buyer.

If no name is verified, output the role-based routes instead:
- call the listed number and ask for the owner/manager by role,
- use the contact form addressed to the owner/manager,
- use the LinkedIn people-search URL in the report,
- use the Google Business Profile message/booking option if present.

Read `references/decision-maker-sources.md` before expanding the search.

---

## Commands

```bash
# Setup (see Phase 0 for the venv)
$PY "$SKILL_DIR/scripts/doctor.py"

# Full pipeline
$PY "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "dentists" --location "Lahore" \
  --areas "Gulberg, DHA, Johar Town" \
  --limit 30 --max-queries 6

# Filtered: no website, established businesses only
$PY "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "gyms" --location "Dubai" \
  --website none --min-rating 4.3 --min-reviews 30 --limit 25

# Re-run only the audit/report from prepared businesses
$PY "$SKILL_DIR/scripts/run_pipeline.py" \
  --input data/lahore_dentists_businesses.json \
  --business "dentists" --location "Lahore" --skip-maps --skip-decisions

# Audit only
$PY "$SKILL_DIR/scripts/audit_sites.py" \
  --input data/lahore_dentists_businesses.json \
  --output data/lahore_dentists_audits.json --workers 6

# Decision makers only
$PY "$SKILL_DIR/scripts/find_decision_makers.py" \
  --input data/lahore_dentists_businesses.json \
  --output data/lahore_dentists_decision_makers.json --max-queries 6

# Report only
$PY "$SKILL_DIR/scripts/build_report.py" \
  --businesses data/lahore_dentists_businesses.json \
  --audits data/lahore_dentists_audits.json \
  --decision-makers data/lahore_dentists_decision_makers.json \
  --output-dir output --prefix lahore_dentists
```

---

## Quality bar

- **No fabricated data.** No invented names, emails, phone numbers, review
  counts, or audit results.
- **Evidence for every finding.** The `evidence` field must quote the actual
  status code, header, element, or text.
- **Outreach lines must stay evidence-based.** `outreach_hook` and
  `opening_line` may only restate a verified finding, rating, review count, or
  website status. Never add an unverified claim about revenue, rankings, or
  competitors.
- **Distinguish "broken" from "blocked".** 403/429 = verify manually.
- **Distinguish "no website" from "no owned website".** A Facebook page is not
  a website, but it is still a contact channel.
- **Do not promise SEO rankings or performance scores.** Report what was
  measured, from where, and when.
- **Respect privacy and platform terms.** Public business contact data only.
  No scraping behind login, no CAPTCHA bypass, no purchased personal data.
- **Keep timestamps.** Every audit and research record includes `verified_at` /
  `researched_at`.
- **Use UTF-8 BOM CSVs** so Excel opens non-English names correctly.

---

## Limitations to state in the final answer

- HTTP/HTML auditing does not measure real-user Core Web Vitals, visual design,
  or JavaScript-rendered content. Use the `web-quality-audit` skill or a
  Lighthouse/browser pass for performance promises.
- Search snippets can be stale or refer to a same-named business. Verify every
  decision maker on the source URL.
- Google Maps DOM changes; if extraction returns zero rows, inspect the page
  and update `maps_extract.js`.
- Websites can block bots; those rows need a manual browser check.
- Decision-maker coverage is strongest for businesses with a team/about page or
  an active LinkedIn presence. Local owner-operated businesses often have no
  public named contact; use role-based outreach.
