---
name: business-lead-intelligence
description: >
  End-to-end local business lead intelligence: browse Google Maps for a
  user-provided niche, location, and criteria (rating, review count, website
  presence, site quality), extract name, phone, published email, address,
  rating, and website, qualify each business, audit whether the website works
  and what is missing/broken and how to fix it, write a detailed finding per
  business, research named decision makers beyond the listed phone, and generate
  an evidence-based outreach hook, opening line, and short personalized cold
  email for each lead. Use when asked to find leads, audit local business
  websites, build a prospect list, filter businesses by rating or website
  status, find owners/founders/managers, or write first-touch outreach.
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
| `output/<prefix>_all_leads.csv` | Every unique business with contact (including published email), site status, score, priority, offer, outreach hook, opening line, and cold-email fields |
| `output/<prefix>_website_opportunity_leads.csv` | Businesses with no site, a broken site, or a weak site |
| `output/<prefix>_site_findings.csv` | One row per finding: severity, evidence, why, fix, page |
| `output/<prefix>_decision_makers.csv` | Named people, role, confidence, source URL, published email |
| `output/<prefix>_FINDINGS.md` | Detailed per-business report with prioritized fixes, outreach routes, and a cold-email draft |
| `output/<prefix>_cold_emails.md` | Copy-ready first-touch emails, grouped by whether a published email exists |
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

### After installation — first-run setup

`npx skills` copies the skill files only. Run the bundled bootstrap once from a
writable workspace:

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs   # any writable directory
python3 "$SKILL_DIR/scripts/bootstrap.py"
```

The bootstrap reads `dependencies.json`, creates `.venv`, installs
`requirements.txt`, installs the companion skills for OpenCode, and installs
the optional Browser Control CLI when npm is available. It is idempotent.
Useful flags: `--dry-run`, `--no-venv`, `--no-skills`, `--no-optional`, and
`--no-browser-control`. Then run `doctor.py`.

The manual steps below are the fallback if the bootstrap cannot run. They do
**not** install Firefox or the Browser Control extension for you.

**1. Check the prerequisites**

| Requirement | Check | Install |
|---|---|---|
| Python 3.10+ | `python3 --version` | <https://www.python.org/downloads/> |
| pip + venv | `python3 -m pip --version` | Debian/Ubuntu: `sudo apt install python3-pip python3-venv` |
| Node.js 22.20+ (npx install only) | `node --version` | <https://nodejs.org/> |
| Firefox (Selenium driver only) | `firefox --version` | see step 4 |
| Browser Control (alternative) | `browser-control status --json` | attach the extension |

Ask the user before running `sudo` or installing system packages.

**2. Create a writable workspace and virtual environment**

The pipeline writes `data/` and `output/` to the current directory. Do not run
it from a read-only skill install directory.

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs   # any writable directory
python3 -m venv .venv
```

On Windows:

```bat
set SKILL_DIR=%USERPROFILE%\.agents\skills\business-lead-intelligence
cd %USERPROFILE%\lead-runs
py -3 -m venv .venv
```

**3. Install the Python packages**

```bash
# macOS/Linux
.venv/bin/pip install -r "$SKILL_DIR/requirements.txt"

# Windows
.venv\Scripts\pip install -r "%SKILL_DIR%\requirements.txt"
```

This installs `requests`, `beautifulsoup4`, and `selenium`. The minimum set is
`requests` and `beautifulsoup4`; `selenium` is needed only for the Firefox
fallback. `pip install requests beautifulsoup4 selenium` is equivalent.

**4. Choose one Maps driver**

*Option A — Browser Control (preferred)*

1. Install the CLI if it is not already available:

   ```bash
   npm install -g @opencode-ai/browser-control
   ```

2. Run `browser-control doctor` and follow its **Next steps** to load the
   bundled `extension/dist` folder as an unpacked extension in the user's
   Chromium-family browser.
3. Open a normal web tab and click the **Browser Control** toolbar button on
   that tab.
4. Keep the tab open, then check `browser-control status --json` and confirm
   `extension.connected` is `true`.

*Option B — Selenium + headless Firefox*

Install Firefox:

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install -y firefox

# Fedora
sudo dnf install -y firefox

# Arch
sudo pacman -S --needed firefox

# macOS
brew install --cask firefox
```

Windows: install from <https://www.mozilla.org/firefox/> or run
`winget install Mozilla.Firefox`.

Verify it:

```bash
firefox --version
```

macOS keeps Firefox at
`/Applications/Firefox.app/Contents/MacOS/firefox`. If the binary is not on
`PATH`, set `FIREFOX_BIN` explicitly:

```bash
export FIREFOX_BIN="/Applications/Firefox.app/Contents/MacOS/firefox"
```

Selenium 4.20+ downloads a matching geckodriver automatically through Selenium
Manager, so no manual geckodriver install is normally needed. If the machine is
offline, install geckodriver manually and put it on `PATH`.

**5. Verify the setup**

```bash
# macOS/Linux
export PY="$PWD/.venv/bin/python"
"$PY" "$SKILL_DIR/scripts/doctor.py"

# Windows
set PY=%CD%\.venv\Scripts\python.exe
"%PY%" "%SKILL_DIR%\scripts\doctor.py"
```

The expected final line is `Ready.` The doctor reports the Python version,
required modules, Browser Control status, Selenium/Firefox availability, and
whether the output directory is writable.

If it fails:

- `ModuleNotFoundError` → use the venv interpreter or repeat step 3.
- `browser-control` missing → use `--driver selenium`, or install and attach it.
- Firefox missing → install it, or set `FIREFOX_BIN`.
- `output` not writable → run from another workspace.

**6. Companion skills**

The bootstrap installs the companions declared in `dependencies.json`:
`cold-email`, `prospecting`, and `web-quality-audit` plus its reference skills
(`performance`, `core-web-vitals`, `accessibility`, `seo`, `best-practices`).
It also installs the optional `browser-control` skill. The manual equivalent is:

```bash
npx skills add coreyhaines31/marketingskills \
  --skill cold-email prospecting \
  -a opencode -g --copy -y

npx skills add addyosmani/web-quality-skills \
  --skill web-quality-audit performance core-web-vitals accessibility seo best-practices \
  -a opencode -g --copy -y
```

The report script can write grounded first drafts on its own. Load
`cold-email` before Phase 6 when it is available; it covers deliverability,
sending domains, follow-up cadence, and reply handling. This skill still owns
the business facts and evidence. Use `web-quality-audit` when a deeper
browser/Lighthouse pass is needed.

All command examples below use `$PY` and `$SKILL_DIR`.

The orchestrator supports three Maps drivers:

```bash
--driver auto       # try Browser Control, then headless Firefox (default)
--driver browser    # Browser Control only
--driver selenium   # headless Firefox + Selenium only
```

If Browser Control is installed but no tab is attached, the first
`browser-control execute` starts the relay and waits up to ~35 seconds. Ask the
user to attach the extension; do not bypass CAPTCHA, consent, or login walls.

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
- **Cold email:** recipient, send status, subject, full body, evidence, and a
  note about whether a named buyer was verified
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
5. How many cold emails are ready to send versus missing a published recipient.
6. Data-quality caveats (blocked sites, missing phones, ambiguous names).

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

## Phase 6 — Write the cold email

This is the final stage, after discovery, audit, findings, and decision-maker
research. Do not write the email before the facts are in the report.

Load the `cold-email` skill (the bootstrap installs it). Use it for outbound
judgment and sending setup. Use this skill's report as the only source of
business facts.

`build_report.py` writes a first-touch draft for every business to
`<prefix>_cold_emails.md` and to the `cold_email_subject` / `cold_email_body`
columns. Treat those as drafts to review, not as approved copy.

### The email shape

One short email, usually 70–120 words:

1. **Specific observation.** Reference something real: a rating and review
   count, no website on the Maps listing, a server error, a slow response, a
   missing contact form, or another cited finding. Use the number or detail from
   the audit, not a vague compliment.
2. **Genuine problem or opportunity.** Explain what that observation means for a
   patient or for the business. Do not invent lost revenue, rankings, or
   competitor claims.
3. **Value of fixing it.** Frame the outcome for the patient: an easier next
   step, a working contact route, a clearer location, a site that loads, or
   trust at the moment they choose a dentist.
4. **Simple, low-pressure offer.** One ask. A mockup, a short fix list, or a
   recovery checklist is enough. Do not ask for a 30-minute call in the first
   email.
5. **Opt-out and sender identity.** Keep the plain-text opt-out. Replace the
   postal-address placeholder before sending.

### Rules

- **No fabricated specifics.** No invented case studies, client names, results,
  revenue figures, rankings, or “I saw your post” claims.
- **No generic sales language.** Avoid “I hope this email finds you well,”
  “quick question,” “synergy,” “leverage,” and long capability paragraphs.
- **No AI tells.** Avoid “It's not X, it's Y” reveals, em-dash pileups, and
  three-item lists that sound generated.
- **One CTA.** The reader should know the single next step.
- **Subject line:** 2–4 words, lowercase, no fake reply/forward prefixes, no
  prospect first name.
- **Greeting:** use a verified owner/manager name only when the source supports
  it. Otherwise use `Hi team,` and note that the buyer is unverified.
- **Recipient:** send only to a published business address. Never guess a
  personal email. If `cold_email_status` is `no_published_email`, use the phone
  or contact form instead.
- **Evidence:** every observation in the body must trace to
  `email_evidence`, `outreach_hook`, a finding, or a Maps field.
- **Sender setup:** send from a secondary domain, verify the address list, keep
  open tracking off, and stop follow-up when someone replies. Those are sending
  tasks, not report tasks.

### Review before sending

Read the draft aloud. Check that it sounds like one person writing to another,
that the specific detail is true, and that the offer is easy to decline. If the
site is already healthy, keep the email light or skip it. If the business has no
published email, do not invent one.

The pipeline never sends email automatically. It writes drafts. Sending is a
separate step that requires explicit approval.

---

## Phase 7 — Send only after explicit approval

Sending is optional. Do not run it until the user has reviewed the dry run and
explicitly approved the recipients and batch size. Read
`references/gmail-sending.md` before the first send.

The sender uses the Gmail API directly. The official Gmail MCP server is not
required; it is currently gated behind the Google Workspace Developer Preview
Program and exposes draft tools, not a send tool.

### 1. Authorize once

```bash
$PY "$SKILL_DIR/scripts/gmail_sender.py" --auth
```

The token is stored in `.opencode/secrets/gmail_token.json` with mode `600`.
Verify the account:

```bash
$PY "$SKILL_DIR/scripts/gmail_sender.py" --whoami
```

### 2. Dry run

```bash
$PY "$SKILL_DIR/scripts/gmail_sender.py" \
  --csv output/<prefix>_all_leads.csv \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address" \
  --show-body
```

This prints each ready recipient and subject. It does not contact Gmail.

### 3. Send

After the user approves the dry run:

```bash
$PY "$SKILL_DIR/scripts/gmail_sender.py" \
  --csv output/<prefix>_all_leads.csv \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address" \
  --send --yes --limit 5 --delay 5
```

### Sending rules

- **No automatic sending.** `run_pipeline.py` and `build_report.py` never send.
- **Explicit approval.** Actual sending requires `--send --yes`; without
  `--yes`, a terminal prompts for `SEND`.
- **Ready rows only.** The sender reads `cold_email_status=ready` and a single
  published email. It skips `no_published_email`, blank, or malformed addresses.
- **No placeholders.** Sender name, company, and postal address must be real
  before `--send`.
- **Small batches first.** Start with 3–5 emails, review replies, then expand.
  Keep `--delay` at 3 seconds or more.
- **No duplicates.** Successful sends are logged in
  `.opencode/secrets/gmail_sent.json`. Do not use `--resend` unless the user
  asks.
- **Stop on failure.** If Gmail returns an error, stop and report it. Do not
  retry blindly or work around a 403/429.
- **Keep the opt-out.** The plain-text opt-out must remain in every email.
- **Respect the law.** Use a real postal address, honor opt-outs immediately,
  and follow local cold-email rules.

---

## Commands

```bash
# Setup (see Phase 0 for the venv)
$PY "$SKILL_DIR/scripts/doctor.py"

# Full pipeline
$PY "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "dentists" --location "Lahore" \
  --areas "Gulberg, DHA, Johar Town" \
  --limit 30 --max-queries 6 \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address"

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
  --output-dir output --prefix lahore_dentists \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address"

# Gmail sending (optional; dry-run first)
$PY "$SKILL_DIR/scripts/gmail_sender.py" --auth
$PY "$SKILL_DIR/scripts/gmail_sender.py" --whoami
$PY "$SKILL_DIR/scripts/gmail_sender.py" \
  --csv output/lahore_dentists_all_leads.csv \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address" \
  --show-body
$PY "$SKILL_DIR/scripts/gmail_sender.py" \
  --csv output/lahore_dentists_all_leads.csv \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address" \
  --send --yes --limit 5 --delay 5
```

The `--sender-*` flags on `run_pipeline.py` and `build_report.py` only fill the
draft signature and opt-out. They do not send anything. If omitted, the drafts
contain bracketed placeholders. `gmail_sender.py` replaces those placeholders at
send time and refuses to send while any remain.

---

## Quality bar

- **No fabricated data.** No invented names, emails, phone numbers, review
  counts, or audit results.
- **Evidence for every finding.** The `evidence` field must quote the actual
  status code, header, element, or text.
- **Outreach lines must stay evidence-based.** `outreach_hook`, `opening_line`,
  and every cold-email observation may only restate a verified finding, rating,
  review count, or website status. Never add an unverified claim about revenue,
  rankings, competitors, or results.
- **Cold email is draft-first.** One specific observation, one genuine problem
  or opportunity, one value statement, one low-pressure ask, and one opt-out. No
  invented proof, no guessed email, and no automatic sending. Sending is Phase 7
  and requires explicit approval.
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
- Cold-email drafts are first-touch only. The pipeline does not verify inboxes,
  warm domains, or manage follow-ups. Use the `cold-email` skill for
  deliverability, cadence, and reply handling.
- Sending is optional and explicit. `gmail_sender.py` sends through the Gmail
  API after OAuth and `--send --yes`; it is not part of the discovery pipeline.
  Start with a small batch and respect Gmail's sending limits.
- The sender name, company, and postal address are placeholders until you pass
  the `--sender-*` flags or edit the drafts. `gmail_sender.py` refuses to send
  while placeholders remain. A postal address and opt-out are required for
  compliant cold email.
- Decision-maker coverage is strongest for businesses with a team/about page or
  an active LinkedIn presence. Local owner-operated businesses often have no
  public named contact; use role-based outreach.
