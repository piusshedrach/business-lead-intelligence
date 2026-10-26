# Audit rules and recommended fixes

This is the reference behind `audit_sites.py`. Each rule has a stable id, a
severity, and a fix the report can hand to a business owner or developer.

## Scoring

Start at 100 and subtract:

| Severity | Points | Typical examples |
|---|---:|---|
| critical | 25 | no site, homepage error, no HTTPS, expired cert, noindex, no viewport, insecure form |
| high | 10 | unreachable page, slow TTFB, missing H1/title, phone mismatch, broken links, malformed WhatsApp |
| medium | 4 | missing meta description, missing schema, thin content, no CTA, no reviews, no privacy |
| low | 1 | missing favicon, missing HSTS/CSP, stale copyright, no cache header |

Grades: A ≥90, B 80–89, C 65–79, D 50–64, F <50. Priority is `critical` when a
critical rule fires or score <35; `high` below 55 or opportunity ≥70; `medium`
below 75; otherwise `low`.

## Availability

| ID | Check | Fix |
|---|---|---|
| `site.none` | No website on the Maps listing | Build a 5-page mobile-first site; add it to the Google Business Profile |
| `site.social_only` | Website field is Facebook/Instagram/etc. | Own domain; use social for updates, not as the primary site |
| `site.directory_only` | Website field is a directory/page builder | Own domain; keep directory listings as citations |
| `availability.http_error` | Homepage 4xx/5xx | Restore page or 301 to a live page; check host/DNS/CMS |
| `availability.ssl_error` | TLS handshake failed | Fix certificate chain/expiry; force HTTPS |
| `availability.timeout` | No response before timeout | Check origin, DNS, firewall, and hosting |
| `availability.dns_or_unreachable` | DNS/TCP failed | Fix DNS records and origin reachability |
| `availability.redirect_loop` | Redirect loop | Remove conflicting redirect rules |
| `availability.blocked` | 403/429 to the auditor | Verify in a browser; relax WAF only if real users are blocked |

## Security

| ID | Check | Fix |
|---|---|---|
| `security.no_https` | Final URL is http:// | Install TLS, 301 redirect, update assets |
| `security.cert_expired` | Certificate expiry < 0 days | Renew and automate renewal |
| `security.cert_expiring` | < 21 days | Renew now; monitor at 30/14/7 days |
| `security.mixed_content` | http:// assets on an https page | Change to https:// or CDN; check console |
| `security.no_hsts` | No HSTS header | `Strict-Transport-Security: max-age=31536000; includeSubDomains` |
| `security.no_csp` | No CSP | Report-only first, then enforce `script-src` |
| `security.no_frame_protection` | No X-Frame-Options / frame-ancestors | `X-Frame-Options: SAMEORIGIN` |
| `security.version_disclosure` | Server version in header | Strip `Server` / `X-Powered-By` |
| `security.form_insecure` | Form action is http:// | Post to HTTPS; redirect page |

## SEO

| ID | Check | Fix |
|---|---|---|
| `seo.title_missing` | No `<title>` | 50–60 chars: service + city + brand |
| `seo.title_length` | <15 or >65 chars | Rewrite to 50–60 |
| `seo.meta_description_missing` | No description | 140–160 chars with offer + proof + CTA |
| `seo.meta_description_length` | <70 or >175 | 140–160 chars |
| `seo.h1_missing` | No H1 | One descriptive H1 with service + city |
| `seo.h1_multiple` | >1 H1 | Keep one H1; use H2/H3 |
| `seo.canonical_missing` | No canonical | Self-referencing canonical per page |
| `seo.noindex` | noindex present | Remove on production; verify in Search Console |
| `seo.lang_missing` | No `lang` | Add correct `lang` to `<html>` |
| `seo.open_graph` | Missing og:title/og:image | Add title, description, 1200×630 image, url, twitter card |
| `seo.brand_in_title` | Brand absent | Add brand at the end if it fits |
| `seo.location_missing` | City absent from title/description | Add city naturally; create location pages if multi-area |
| `seo.schema_missing` | No JSON-LD | Add LocalBusiness + core fields |
| `seo.schema_local_business` | Only generic schema | Add the most specific LocalBusiness subtype |
| `seo.schema_incomplete` | Missing tel/address/hours/geo/image | Complete the object; validate in Rich Results Test |
| `seo.broken_links` | 404/410/5xx in sampled links | Fix or 301 to the closest live page |
| `content.thin` | <300 visible words | Add services, proof, pricing/insurance, FAQs (500–900 useful words) |

## Mobile

| ID | Check | Fix |
|---|---|---|
| `mobile.no_viewport` | No viewport meta | `width=device-width, initial-scale=1` |
| `mobile.viewport_fixed` | Fixed-width viewport | Responsive layout; test 360/390/768px |

## Conversion and trust

| ID | Check | Fix |
|---|---|---|
| `conversion.no_tel_link` | No `tel:` link | Wrap phone in `tel:` and repeat in header/contact |
| `conversion.no_contact_method` | No form and no email | Add short form + monitored inbox |
| `conversion.form_fields` | Form has no phone/email field | Require one reply channel |
| `conversion.no_whatsapp` | No WhatsApp link | Add floating WhatsApp with pre-filled message |
| `conversion.no_cta` | No booking/quote/contact CTA | One primary CTA above the fold, repeated after proof |
| `conversion.no_contact_page` | No contact page link | Create /contact with NAP, form, map, hours |
| `trust.no_social_proof` | No reviews/testimonials | Add 3–6 reviews + Google profile link |
| `trust.no_address` | No address/location block | Full address, map embed, parking notes |
| `trust.no_hours` | No opening hours | Add hours + `openingHoursSpecification`; match Maps |
| `trust.no_privacy` | No privacy/terms link | Plain-language privacy policy near every form |
| `trust.stale_copyright` | Copyright < current year − 1 | Update or make dynamic |
| `trust.phone_mismatch` | Website phone ≠ Maps | One primary number everywhere |
| `trust.address_mismatch` | Website address ≠ Maps | Exact address in footer + contact page |

## Performance

| ID | Check | Fix |
|---|---|---|
| `performance.slow_ttfb` | >3s | CDN + page cache; target <1s from target market |
| `performance.ttfb` | >1.5s | CDN/page cache; compress HTML |
| `performance.html_size` | >500 KB HTML | Move inline CSS/JS out; remove unused blocks |
| `performance.requests` | >25 scripts + stylesheets | Bundle/minify, defer non-critical JS, remove plugins |
| `performance.no_compression` | No Content-Encoding | Enable gzip/Brotli for text assets |
| `performance.no_lazy_loading` | >8 images, 0 lazy | `loading="lazy"` below the fold; eager hero |
| `performance.no_cache_headers` | No Cache-Control | Short HTML cache; immutable hashed assets |

## Accessibility

| ID | Check | Fix |
|---|---|---|
| `accessibility.image_alt` | >30% images without alt | Descriptive alt; empty alt for decorative |

## Manual checks the auditor cannot automate

These must be verified in a browser before making strong claims:

- Real-user Core Web Vitals (LCP, INP, CLS) and field data.
- Visual design quality, contrast, font sizes, tap-target spacing.
- JavaScript-rendered content and client-side routing.
- Form delivery (submit a test enquiry and confirm receipt).
- Booking flow, payment flow, and third-party embeds.
- Responsive behaviour at 360px, 390px, 768px, 1024px, 1440px.
- Cookie/consent behaviour and analytics firing.
- Whether a 403 is bot protection or a real customer-facing block.
