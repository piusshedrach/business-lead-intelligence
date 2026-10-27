# Business Lead Intelligence

An evidence-first local lead pipeline for AI agents. Give it a niche, a
location, and optional criteria; it discovers businesses on Google Maps, audits
their websites, finds public decision makers, and produces proposal-ready
findings, outreach angles, and short personalized cold-email drafts.

Built for **OpenCode** and compatible with any agent that supports the
[Agent Skills](https://agentskills.io) layout.

## Install

### npx skills (recommended)

Requires Node.js 22.20+.

```bash
npx skills add piusshedrach/business-lead-intelligence \
  --skill business-lead-intelligence \
  -a opencode -g --copy
```

This installs to `~/.agents/skills/business-lead-intelligence`, which OpenCode
discovers automatically. `~/.config/opencode/skills` is also supported.

### Manual

```bash
git clone https://github.com/piusshedrach/business-lead-intelligence.git
mkdir -p ~/.agents/skills
cp -R business-lead-intelligence/skills/business-lead-intelligence \
  ~/.agents/skills/
```

OpenCode also discovers `~/.agents/skills`, `~/.claude/skills`, and project
`.opencode/skills` / `.agents/skills` directories.

## Setup after installing

`npx skills` copies the skill files only. It does **not** install Python
packages, Firefox, or the Browser Control extension. Complete these steps once.

**1. Check prerequisites**

- Python **3.10+** with `pip` and `venv`
- Node.js **22.20+** only for the `npx skills` install
- Firefox for the Selenium fallback, or the Browser Control extension

```bash
python3 --version
python3 -m pip --version
```

Debian/Ubuntu, if `venv` is missing:

```bash
sudo apt install python3-pip python3-venv
```

**2. Create a writable workspace**

The pipeline writes `data/` and `output/` to the current directory, so do not
run it from the skill install directory.

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs
python3 -m venv .venv
```

Windows:

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

This installs `requests`, `beautifulsoup4`, and `selenium`. Only the first two
are required for auditing and reporting; Selenium is for the Firefox Maps
fallback.

**4. Choose a Maps driver**

Browser Control (preferred): install the CLI and follow its setup check:

```bash
npm install -g @opencode-ai/browser-control
browser-control doctor
```

Run `browser-control doctor` and follow its **Next steps** to load the bundled
`extension/dist` folder as an unpacked extension. Then open a browser tab, click
the Browser Control toolbar button, and confirm:

```bash
browser-control status --json
```

Selenium fallback: install Firefox.

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

Windows: <https://www.mozilla.org/firefox/> or `winget install Mozilla.Firefox`.

Verify and, if needed, point Selenium at the binary:

```bash
firefox --version
export FIREFOX_BIN="/Applications/Firefox.app/Contents/MacOS/firefox"  # macOS example
```

Selenium 4.20+ downloads geckodriver automatically via Selenium Manager. If the
machine is offline, install geckodriver manually and put it on `PATH`.

**5. Verify**

```bash
.venv/bin/python "$SKILL_DIR/scripts/doctor.py"
```

Expected final line: `Ready.` The doctor checks the Python modules, Browser
Control, Firefox, and output-directory access, and prints hints for anything
missing.

**6. Optional companion: cold-email**

The report generator writes grounded first drafts on its own. For stronger
outbound guidance, install the companion skill once:

```bash
npx skills add coreyhaines31/marketingskills \
  --skill cold-email \
  -a opencode -g --copy
```

It adds deliverability, sending-domain, follow-up, and reply-handling guidance.
This skill still owns the business facts and evidence.

## Use

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

Filters:

```bash
--website none --min-rating 4.3 --min-reviews 30   # no website, established
--website has --max-score 50                      # weak live sites
--priority critical,high                          # hottest audits
```

Drivers: `--driver auto` (default), `browser`, or `selenium`.

The agent can also load the skill by ID (`business-lead-intelligence`) and
follow its phases directly.

## Outputs

- `<prefix>_FINDINGS.md` — per-business report with evidence, fixes, contacts,
  decision makers, an outreach angle, and a cold-email draft
- `<prefix>_all_leads.csv` — all leads with phone, published email, site
  status, score, priority, offer, outreach hook, opening line, subject, body,
  cold-email status, notes, and email evidence
- `<prefix>_website_opportunity_leads.csv` — no-site, broken, or weak-site leads
- `<prefix>_site_findings.csv` — one row per finding
- `<prefix>_decision_makers.csv` — named people with role and source URL
- `<prefix>_cold_emails.md` — copy-ready first-touch emails, grouped by whether
  a published email exists

## Repository layout

```text
skills/business-lead-intelligence/
├── SKILL.md
├── README.md
├── requirements.txt
├── references/
└── scripts/
tests/
└── test_smoke.py
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r skills/business-lead-intelligence/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python skills/business-lead-intelligence/scripts/doctor.py --allow-no-driver
```

## Safety and limits

- Public business data only. No login walls, CAPTCHA bypass, purchased data, or
  invented emails.
- Google Maps does not expose email addresses; `email` is only populated when
  the business publishes one. The pipeline never guesses a personal address.
  Drafts without a recipient are marked `no_published_email`.
- Cold-email drafts are first-touch only. The pipeline does not verify inboxes,
  warm sending domains, send messages, or manage follow-ups. Use the
  `cold-email` skill for deliverability, cadence, and reply handling.
- Replace the sender name, company, and postal-address placeholders before
  sending. A postal address and opt-out are required for compliant cold email.
- Audits are HTTP/HTML based, not real-user Core Web Vitals or visual design.
- 403/429 means "blocked to the auditor", not "site down".
- Respect Google's terms, local outreach rules, and opt-outs.

## License

MIT. See [LICENSE](LICENSE). Change the copyright holder if you publish under
a different name or organization.
