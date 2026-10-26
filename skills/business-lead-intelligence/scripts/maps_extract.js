/**
 * Google Maps extractor for Browser Control.
 *
 * Run with:
 *   browser-control execute --file maps_extract.js
 *
 * It reads maps-config.json from the system temp directory
 * (/tmp/opencode on most Linux/macOS systems):
 * {
 *   "queries": ["dentists in Lahore", "dental clinic in Gulberg Lahore"],
 *   "limit_per_query": 20,
 *   "output": "/abs/path/businesses.json",
 *   "max_scrolls": 8
 * }
 *
 * Writes a JSON object: {query, extracted_at, count, businesses: [...]}.
 * No login, CAPTCHA bypass, or anti-bot evasion is performed.
 */
const tmpBase =
  (typeof process !== "undefined" &&
    process.env &&
    (process.env.TMPDIR || process.env.TEMP || process.env.TMP)) ||
  "/tmp";
const tmpDir = `${String(tmpBase).replace(/[\\/]+$/, "")}/opencode`;
const cfgPath = `${tmpDir}/maps-config.json`;
const cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
const queries = cfg.queries || [];
const limitPerQuery = cfg.limit_per_query || 20;
const maxScrolls = cfg.max_scrolls || 8;
const output = cfg.output || `${tmpDir}/businesses.json`;

if (!queries.length) {
  throw new Error("No queries in " + cfgPath);
}

const clean = (value) =>
  String(value || "")
    .replace(/[\ue000-\uf8ff]/g, "")
    .replace(/\u200b/g, " ")
    .replace(/\s+/g, " ")
    .trim();

const placeKey = (url, name, address) => {
  const m = String(url || "").match(/!1s([0-9a-fA-Fx]+:[0-9a-fA-Fx]+)/);
  if (m) return m[1];
  return `${clean(name).toLowerCase()}|${clean(address).toLowerCase()}`;
};

async function dismissConsent() {
  const labels = [
    "Accept all",
    "Reject all",
    "I agree",
    "Accept",
    "Agree to all",
  ];
  for (const label of labels) {
    try {
      const button = page.getByRole("button", { name: label, exact: false }).first();
      if (await button.count()) {
        await button.click({ timeout: 2500 });
        await page.waitForTimeout(800);
        return true;
      }
    } catch (_) {}
  }
  return false;
}

async function collectPlaceLinks(query) {
  const url = `https://www.google.com/maps/search/${encodeURIComponent(query)}`;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(1800);
  await dismissConsent();
  await page.waitForTimeout(1200);

  const feed = page.locator('div[role="feed"]').first();
  if (await feed.count()) {
    let stable = 0;
    let previous = 0;
    for (let i = 0; i < maxScrolls; i++) {
      const count = await page.locator('a[href*="/maps/place/"]').count();
      if (count >= limitPerQuery) break;
      if (count === previous) stable += 1;
      else stable = 0;
      if (stable >= 3) break;
      previous = count;
      try {
        await feed.evaluate((el) => el.scrollTo(0, el.scrollHeight));
      } catch (_) {
        await page.mouse.wheel(0, 2400);
      }
      await page.waitForTimeout(1100);
    }
  }

  const cards = await page
    .locator('a[href*="/maps/place/"]')
    .evaluateAll((nodes) =>
      nodes.map((a) => ({
        href: a.href,
        label: a.getAttribute("aria-label") || a.innerText || "",
      })),
    );

  const seen = new Set();
  const links = [];
  for (const card of cards) {
    if (!card.href || seen.has(card.href)) continue;
    seen.add(card.href);
    links.push(card);
    if (links.length >= limitPerQuery) break;
  }

  if (!links.length && page.url().includes("/maps/place/")) {
    links.push({ href: page.url(), label: "" });
  }
  return links;
}

async function extractDetail(cardLabel) {
  await page.waitForTimeout(1400);
  const detail = await page.evaluate((fallbackLabel) => {
    const clean = (value) =>
      String(value || "")
        .replace(/[\ue000-\uf8ff]/g, "")
        .replace(/\u200b/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    const one = (sel) => document.querySelector(sel);
    const txt = (el) => clean(el && (el.innerText || el.textContent));
    const aria = (el) => clean(el && el.getAttribute("aria-label"));
    const item = (id) =>
      one(`button[data-item-id="${id}"]`) ||
      one(`a[data-item-id="${id}"]`) ||
      one(`[data-item-id="${id}"]`);

    let name = txt(one("h1"));
    if (!name) name = clean(document.title.split(" - ")[0]);
    if (!name) name = clean(fallbackLabel).split("\n")[0];

    const addressEl = item("address");
    let address = aria(addressEl) || txt(addressEl);
    address = clean(address.replace(/^Address:\s*/i, ""));

    const phoneEl =
      one('button[data-item-id^="phone:tel:"]') ||
      one('a[href^="tel:"]') ||
      one('button[aria-label^="Phone:"]');
    let phone =
      aria(phoneEl) ||
      txt(phoneEl) ||
      clean((phoneEl && phoneEl.getAttribute("href")) || "").replace(/^tel:/i, "");
    phone = clean(phone.replace(/^Phone:\s*/i, ""));

    const websiteEl =
      one('a[data-item-id="authority"]') ||
      one('a[aria-label^="Website:"]') ||
      one('a[data-item-id^="authority"]');
    const website = websiteEl ? websiteEl.href : "";

    const categoryEl =
      one('button[jsaction*="pane.rating.category"]') ||
      one("button.DkEaL") ||
      one('button[jsaction*="category"]');
    const category = txt(categoryEl) || aria(categoryEl);

    const ratingEl =
      one('div.F7nice span[aria-hidden="true"]') ||
      one("span.ceNzKf") ||
      one('span[aria-label*="stars"]');
    let rating = txt(ratingEl) || aria(ratingEl);
    const ratingMatch = rating.match(/[\d.]+/);
    rating = ratingMatch ? ratingMatch[0] : "";

    const reviewEl =
      one('div.F7nice span[aria-label*="review"]') ||
      one('button[aria-label*="review"]') ||
      one('span[aria-label*="review"]');
    let reviews = aria(reviewEl) || txt(reviewEl);
    const reviewMatch = String(reviews).match(/[\d,]+/);
    reviews = reviewMatch ? reviewMatch[0].replace(/,/g, "") : "";

    const hoursEl = item("oh");
    const hours = aria(hoursEl) || txt(hoursEl);

    const plusEl = item("oloc");
    const plusCode = aria(plusEl) || txt(plusEl);

    const url = location.href;
    const idMatch = url.match(/!1s([0-9a-fA-Fx]+:[0-9a-fA-Fx]+)/);
    const coordMatch = url.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
    const mainText = clean((one('[role="main"]') || document.body).innerText || "");

    return {
      name,
      category,
      address,
      phone,
      website,
      rating,
      reviews,
      hours,
      plus_code: plusCode,
      place_url: url.split("&")[0],
      place_id: idMatch ? idMatch[1] : "",
      latitude: coordMatch ? coordMatch[1] : "",
      longitude: coordMatch ? coordMatch[2] : "",
      description: mainText.slice(0, 900),
    };
  }, cardLabel || "");
  return detail;
}

const all = [];
const seen = new Set();
const report = [];

for (const query of queries) {
  console.log(`[maps] query: ${query}`);
  let links = [];
  try {
    links = await collectPlaceLinks(query);
  } catch (err) {
    console.log(`[maps] query failed: ${query}: ${err.message}`);
    report.push({ query, error: String(err.message || err), found: 0 });
    continue;
  }
  console.log(`[maps] ${links.length} place link(s)`);
  let added = 0;
  for (const link of links) {
    try {
      await page.goto(link.href, { waitUntil: "domcontentloaded", timeout: 45000 });
      await dismissConsent();
      const detail = await extractDetail(link.label);
      if (!detail.name) continue;
      const key = placeKey(detail.place_url, detail.name, detail.address);
      if (seen.has(key)) continue;
      seen.add(key);
      detail.query = query;
      detail.source_query = query;
      all.push(detail);
      added += 1;
    } catch (err) {
      console.log(`[maps] detail failed: ${link.href}: ${err.message}`);
    }
  }
  report.push({ query, found: links.length, added });
  console.log(`[maps] added ${added}; total ${all.length}`);
}

const payload = {
  extracted_at: new Date().toISOString(),
  queries,
  count: all.length,
  report,
  businesses: all,
};
const outDir = output.includes("/") ? output.slice(0, output.lastIndexOf("/")) : ".";
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(output, JSON.stringify(payload, null, 2), "utf8");
console.log(`[maps] wrote ${all.length} businesses -> ${output}`);
return { count: all.length, output, report };
