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
6. **Write the email** — produces a short first-touch email per business with
   one specific observation, a genuine problem or opportunity, the value of
   fixing it, and one low-pressure ask. Drafts are grounded in the report and
   never sent automatically.

## Requirements

- Python **3.10+**
- `requests`, `beautifulsoup4`
- One Maps driver:
  - the [Browser Control](https://www.npmjs.com/package/@opencode-ai/browser-control)
    extension attached to a real browser tab, **or**
  - `selenium` + a system Firefox (headless fallback)
- Node.js 22.20+ only if you install with `npx skills`

No paid API keys are required.

## Install with npx skills

```bash
# Global install for OpenCode
npx skills add piusshedrach/business-lead-intelligence --skill business-lead-intelligence -a opencode -g --copy
```

`npx skills` copies the skill directory. It does **not** install Python
dependencies, so complete the setup below once.

## Manual install

```bash
git clone https://github.com/piusshedrach/business-lead-intelligence.git
mkdir -p ~/.agents/skills
cp -R business-lead-intelligence/skills/business-lead-intelligence ~/.agents/skills/
```

OpenCode also discovers `~/.agents/skills`, `~/.claude/skills`, and project
`.opencode/skills` / `.agents/skills` directories.

## Setup after installing

`npx skills` copies the skill files only. Run the bundled bootstrap once from a
writable workspace:

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs   # any writable directory
python3 "$SKILL_DIR/scripts/bootstrap.py"
```

It creates `.venv`, installs `requirements.txt`, installs the companion skills
declared in `dependencies.json`, and installs the optional Browser Control CLI
when npm is available. It is idempotent. Use `--dry-run`, `--no-venv`,
`--no-skills`, `--no-optional`, or `--no-browser-control` to change what it
does. Then run `doctor.py`.

The manual steps below are the fallback if the bootstrap cannot run. They do
**not** install Firefox or the Browser Control extension for you.

### 1. Python

Requires Python 3.10+ with `pip` and `venv`.

```bash
python3 --version
python3 -m pip --version
```

Debian/Ubuntu, if `venv` is missing:

```bash
sudo apt install python3-pip python3-venv
```

### 2. Create a workspace

The pipeline writes `data/` and `output/` to the current directory, so use a
writable workspace, not the read-only skill install directory.

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs
python3 -m venv .venv
```

On Windows:

```bat
set SKILL_DIR=%USERPROFILE%\.agents\skills\business-lead-intelligence
cd %USERPROFILE%\lead-runs
py -3 -m venv .venv
```

### 3. Install the Python packages

```bash
# macOS/Linux
.venv/bin/pip install -r "$SKILL_DIR/requirements.txt"

# Windows
.venv\Scripts\pip install -r "%SKILL_DIR%\requirements.txt"
```

This installs `requests`, `beautifulsoup4`, and `selenium`. Only `requests` and
`beautifulsoup4` are required for the audit/report stages; `selenium` is needed
for the Firefox Maps fallback.

### 4. Install Firefox (Selenium fallback)

Skip this if you will use the Browser Control extension.

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

Verify:

```bash
firefox --version
```

On macOS, Firefox lives at
`/Applications/Firefox.app/Contents/MacOS/firefox`. If it is not on `PATH`, set:

```bash
export FIREFOX_BIN="/Applications/Firefox.app/Contents/MacOS/firefox"
```

Selenium 4.20+ downloads geckodriver automatically through Selenium Manager. If
the machine is offline, install geckodriver manually and put it on `PATH`.

### 5. Verify

```bash
# macOS/Linux
.venv/bin/python "$SKILL_DIR/scripts/doctor.py"

# Windows
.venv\Scripts\python "%SKILL_DIR%\scripts\doctor.py"
```

Expected final line: `Ready.` If it fails, the output tells you whether Python
modules, Browser Control, Firefox, or the output directory need attention.

### 6. Companion skills

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

The report generator writes grounded drafts on its own. The `cold-email` skill
adds deliverability, sending-domain, follow-up, and reply-handling guidance.
`web-quality-audit` is there for a deeper browser/Lighthouse pass. This skill
still owns the business facts and evidence.

### 7. Run

```bash
.venv/bin/python "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "dentists" \
  --location "Lahore" \
  --areas "Gulberg, DHA, Johar Town" \
  --limit 25 \
  --sender-name "Your Name" \
  --sender-company "Your Company" \
  --sender-address "Your postal address"
```

The `--sender-*` flags fill the email signature and postal address. They do not
send anything. If omitted, the drafts contain bracketed placeholders.

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
| `output/<prefix>_FINDINGS.md` | Per-business report: score, findings, fixes, contacts, decision makers, outreach angle, cold-email draft |
| `output/<prefix>_all_leads.csv` | Every business with phone, published email, site status, score, priority, offer, outreach hook, subject, body, cold-email status, notes, evidence |
| `output/<prefix>_website_opportunity_leads.csv` | No-site, broken, or weak-site leads |
| `output/<prefix>_site_findings.csv` | One row per finding with evidence and fix |
| `output/<prefix>_decision_makers.csv` | Named people with role, confidence, source URL |
| `output/<prefix>_cold_emails.md` | Copy-ready first-touch emails, grouped by whether a published email exists |
| `data/*.json` | Raw discovery, audit, and research records |

## Safety and limits

- Public business data only. No login walls, CAPTCHA bypass, purchased data, or
  invented emails.
- Google Maps does not expose email addresses. `email` is filled only when the
  business publishes one on its own site. The pipeline never guesses a personal
  address; drafts without a recipient are marked `no_published_email`.
- Cold-email drafts are first-touch only. The pipeline does not verify inboxes,
  warm sending domains, send messages, or manage follow-ups. Use the
  `cold-email` skill for deliverability, cadence, and reply handling.
- Replace the sender name, company, and postal-address placeholders before
  sending. A postal address and opt-out are required for compliant cold email.
- HTTP/HTML auditing does not measure real-user Core Web Vitals or visual design.
- 403/429 means "blocked to the auditor", not "site down".
- Maps and search DOMs change. If extraction returns zero rows, inspect one page
  and update the selectors in `scripts/`.
- Respect Google's terms and local outreach rules. You are responsible for how
  you use the output.

## License

MIT. See the repository's `LICENSE` file.
