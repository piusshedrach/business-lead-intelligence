# Business Lead Intelligence

An evidence-first local lead pipeline for AI agents. Give it a niche, a location,
and optional criteria; it discovers businesses on Google Maps, audits their
websites, finds public decision makers, and writes proposal-ready findings and
outreach angles.

Works with **OpenCode** and any agent that supports the
[Agent Skills](https://agentskills.io) layout.

## What it does

1. **Discover** — searches Google Maps for the niche, sub-areas, and filters,
   then extracts name, category, address, phone, website, rating, reviews,
   hours, and place URL. Deduplicates by place ID.
2. **Qualify** — classifies each website as none / social-only / directory /
   owned, then probes DNS, TLS, HTTP, redirects, and the homepage.
3. **Audit** — checks security, SEO, mobile, conversion, trust, schema,
   performance, contact pages, and sampled broken links. Every finding includes
   evidence, impact, fix, effort, and the page URL.
4. **Find people** — scans team/about pages, JSON-LD, and public search results
   for owners, founders, directors, and managers. Every candidate carries a
   source URL and confidence. No guessed emails, no LinkedIn login scraping.
5. **Report** — scores, prioritizes, recommends an offer, and writes an
   evidence-based outreach hook and draft opening line per business.

## Requirements

- Python **3.10+**
- `requests`, `beautifulsoup4`
- One Maps driver:
  - the [Browser Control](https://github.com/anomalyco/opencode) extension
    attached to a real browser tab, **or**
  - `selenium` + a system Firefox (headless fallback)
- Node.js 22.20+ only if you install with `npx skills`

No paid API keys are required.

## Install with npx skills

```bash
# Global install for OpenCode
npx skills add <owner>/<repo> --skill business-lead-intelligence -a opencode -g --copy
```

`npx skills` copies the skill directory. It does **not** install Python
dependencies, so complete the setup below once.

## Manual install

```bash
git clone https://github.com/<owner>/<repo>.git
mkdir -p ~/.agents/skills
cp -R <repo>/skills/business-lead-intelligence ~/.agents/skills/
```

OpenCode also discovers `~/.agents/skills`, `~/.claude/skills`, and project
`.opencode/skills` / `.agents/skills` directories.

## Setup

Run from a writable workspace; the pipeline writes `data/` and `output/` there.

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs
python3 -m venv .venv
.venv/bin/pip install -r "$SKILL_DIR/requirements.txt"
.venv/bin/python "$SKILL_DIR/scripts/doctor.py"
```

On Windows use `.venv\Scripts\python` and `.venv\Scripts\pip`.

## Run

```bash
.venv/bin/python "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "dentists" \
  --location "Lahore" \
  --areas "Gulberg, DHA, Johar Town" \
  --limit 25
```

Common filters:

```bash
# No website, established businesses
--website none --min-rating 4.3 --min-reviews 30

# Weak live sites
--website has --max-score 50

# Only the hottest audits
--priority critical,high
```

Maps driver selection:

- `--driver auto` (default): Browser Control, then headless Firefox
- `--driver browser`: Browser Control only
- `--driver selenium`: headless Firefox only

Prepared input (skip Maps):

```bash
.venv/bin/python "$SKILL_DIR/scripts/run_pipeline.py" \
  --input data/businesses.json --skip-maps \
  --business "dentists" --location "Lahore"
```

## Outputs

| File | Contents |
|---|---|
| `output/<prefix>_FINDINGS.md` | Per-business report: score, findings, fixes, contacts, decision makers, outreach angle |
| `output/<prefix>_all_leads.csv` | Every business with phone, published email, site status, score, priority, offer, outreach hook |
| `output/<prefix>_website_opportunity_leads.csv` | No-site, broken, or weak-site leads |
| `output/<prefix>_site_findings.csv` | One row per finding with evidence and fix |
| `output/<prefix>_decision_makers.csv` | Named people with role, confidence, source URL |
| `data/*.json` | Raw discovery, audit, and research records |

## Safety and limits

- Public business data only. No login walls, CAPTCHA bypass, purchased data, or
  invented emails.
- Google Maps does not expose email addresses. `email` is filled only when the
  business publishes one on its own site.
- HTTP/HTML auditing does not measure real-user Core Web Vitals or visual design.
- 403/429 means "blocked to the auditor", not "site down".
- Maps and search DOMs change. If extraction returns zero rows, inspect one page
  and update the selectors in `scripts/`.
- Respect Google's terms and local outreach rules. You are responsible for how
  you use the output.

## License

MIT. See the repository's `LICENSE` file.
