# Decision-maker research: sources, confidence, and limits

## What counts as a decision maker

Prioritize by ability to approve a website/marketing spend:

1. **Owner / Founder / Co-Founder / Proprietor** — best for local SMBs.
2. **CEO / Managing Director / Director / Partner / Principal** — best for
   multi-site clinics, agencies, and companies.
3. **Practice Manager / Clinic Manager / General Manager / Operations Manager**
   — often owns the website and vendor relationships.
4. **Marketing Manager / Head of Marketing / Business Development Manager** —
   owns the site, SEO, and lead flow.
5. **Lead Doctor / Senior Consultant** — clinical decision maker in practices.

Avoid generic `info@`, `admin@`, and reception staff unless nothing else exists.
A role-based route is better than a wrong name.

## Lawful sources, in order

### 1. The business's own website (highest confidence)

- `/about`, `/about-us`, `/team`, `/our-team`, `/leadership`, `/management`,
  `/staff`, `/people`, `/founders`, `/doctors`, `/faculty`
- JSON-LD `Person` (`name`, `jobTitle`, `email`, `sameAs`)
- Author bylines on blog posts
- Press/media pages
- Footer `mailto:` addresses and named contacts

Record: name, role, page URL, exact evidence sentence, published email (only if
it appears on the page), LinkedIn URL if linked.

### 2. Public search results (medium/low confidence)

Use search-result titles and snippets as leads, not proof:

- `"Business Name" "City" owner OR founder OR CEO`
- `"Business Name" "City" director OR manager`
- `"Business Name" "managing director" OR "practice manager"`
- `site:linkedin.com/in "Business Name" "City"`
- `"Business Name" "City" email contact`
- `"Business Name" "City" facebook about`

For LinkedIn, read only the public search snippet. Do **not** log in, scrape the
profile page, or bypass auth. A LinkedIn result is a pointer to verify manually.

### 3. Public social/business profiles

- Facebook Page → Page transparency / About / posts announcing hires
- Instagram bio → owner tags, email, website
- LinkedIn company page → employees section (public only)
- Google Business Profile → owner responses, booking/message options
- Chamber of commerce, trade association, exhibitor lists
- Company registry / incorporation notices (where public)
- Local news, podcasts, award lists, event speaker bios

## Confidence levels

| Level | Criteria | Use |
|---|---|---|
| `high` | Name + role on the company's own page, or JSON-LD Person, or a public profile that clearly matches the business | Can address outreach by name |
| `medium` | Name + role in a search snippet or LinkedIn result title that includes the business name | Verify before using |
| `low` | Name and role appear in the same snippet but the business match is weak or ambiguous | Research only; do not put in a sequence |

If two businesses share a name or a person appears with a different company,
mark it `low` and require manual confirmation. Never merge people by first name
alone.

## Email rules

- Only output an email that was **published** on a fetched page or an official
  public profile.
- Never guess `firstname@domain.com`, `info@`, or a pattern unless the user
  explicitly asks for pattern hypotheses. If you do, label it
  `unverified_pattern` and do not mix it with verified emails.
- Do not use purchased lists, data brokers, or scraped personal emails.
- Do not expose personal mobile numbers that are not published for business
  contact.

## Contact routes to include when no name is found

1. Call the listed business number and ask for the owner/founder/practice
   manager by role (not by name).
2. Use the website contact form; address it to "the owner or practice manager".
3. Use the LinkedIn people-search URL for the business + city.
4. Use the Google Business Profile message/booking option if enabled.
5. Visit in person for very local SMBs; ask for the manager on duty.
6. Send a physical letter to the registered/business address for high-value
   targets.

## Verification checklist before outreach

- [ ] The person still works there (profile/company page is current).
- [ ] The role actually owns the website/marketing decision.
- [ ] The source URL opens and contains the evidence.
- [ ] The business name and location match the lead.
- [ ] The email is published, not inferred.
- [ ] The contact route is appropriate for the channel (no cold WhatsApp to a
      personal number, no LinkedIn automation).
- [ ] Local privacy/outreach rules are respected (opt-out, B2B rules, GDPR/PECR
      where applicable).

## Red flags

- A name only appears in a directory aggregator with no source.
- The "founder" left years ago and now runs a different company.
- The website is a franchise template; the local franchisee is the buyer, not
  the global brand.
- The LinkedIn profile is private or the snippet is truncated.
- A generic `info@` inbox is the only route; deliverability and ownership are
  unknown.
- The business is a sole practitioner with no public name; role-based outreach
  is the honest answer.
