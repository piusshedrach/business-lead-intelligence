# Business Lead Intelligence

An evidence-first local lead pipeline for AI agents. Give it a niche, a
location, and optional criteria; it discovers businesses on Google Maps, audits
their websites, finds public decision makers, and produces proposal-ready
findings and outreach angles.

Built for **OpenCode** and compatible with any agent that supports the
[Agent Skills](https://agentskills.io) layout.

## Install

### npx skills (recommended)

Requires Node.js 22.20+.

```bash
npx skills add <owner>/business-lead-intelligence \
  --skill business-lead-intelligence \
  -a opencode -g --copy
```

This installs to `~/.agents/skills/business-lead-intelligence`, which OpenCode
discovers automatically. `~/.config/opencode/skills` is also supported.

### Manual

```bash
git clone https://github.com/<owner>/business-lead-intelligence.git
mkdir -p ~/.agents/skills
cp -R business-lead-intelligence/skills/business-lead-intelligence \
  ~/.agents/skills/
```

OpenCode also discovers `~/.agents/skills`, `~/.claude/skills`, and project
`.opencode/skills` / `.agents/skills` directories.

## Setup

Python dependencies are not installed by `npx skills`. Run this once from a
writable workspace:

```bash
export SKILL_DIR="$HOME/.agents/skills/business-lead-intelligence"
cd ~/lead-runs
python3 -m venv .venv
.venv/bin/pip install -r "$SKILL_DIR/requirements.txt"
.venv/bin/python "$SKILL_DIR/scripts/doctor.py"
```

Requirements: Python 3.10+, `requests`, `beautifulsoup4`, and either the
Browser Control extension attached to a browser tab or `selenium` + Firefox.

## Use

```bash
.venv/bin/python "$SKILL_DIR/scripts/run_pipeline.py" \
  --business "dentists" \
  --location "Lahore" \
  --areas "Gulberg, DHA, Johar Town" \
  --limit 25
```

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
  decision makers, and an outreach angle
- `<prefix>_all_leads.csv` — all leads with phone, published email, site
  status, score, priority, offer, outreach hook, opening line
- `<prefix>_website_opportunity_leads.csv` — no-site, broken, or weak-site leads
- `<prefix>_site_findings.csv` — one row per finding
- `<prefix>_decision_makers.csv` — named people with role and source URL

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
  the business publishes one.
- Audits are HTTP/HTML based, not real-user Core Web Vitals or visual design.
- 403/429 means "blocked to the auditor", not "site down".
- Respect Google's terms, local outreach rules, and opt-outs.

## License

MIT. See [LICENSE](LICENSE). Change the copyright holder if you publish under
a different name or organization.
