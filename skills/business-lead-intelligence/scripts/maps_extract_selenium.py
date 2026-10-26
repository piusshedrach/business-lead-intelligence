#!/usr/bin/env python3
"""Headless Firefox fallback for Google Maps extraction.

Use this only when Browser Control cannot attach to the user's browser. It uses
the system Firefox through Selenium and the same config file as maps_extract.js:

  maps-config.json in the system temp directory
  (/tmp/opencode on most Linux/macOS systems)
  {"queries": [...], "limit_per_query": 20, "output": "...", "max_scrolls": 8}

Google may show a consent screen or block headless sessions. Handle consent
only; do not bypass CAPTCHAs or access controls. If zero rows are returned,
use Browser Control with a real tab instead.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

TMP_DIR = Path(tempfile.gettempdir()) / "opencode"

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Selenium is not installed. Install it with: pip install selenium\n" + str(exc)
    )


DETAIL_JS = r"""
const clean = (v) => String(v || "").replace(/[\ue000-\uf8ff]/g, "").replace(/\u200b/g, " ").replace(/\s+/g, " ").trim();
const one = (s) => document.querySelector(s);
const txt = (el) => clean(el && (el.innerText || el.textContent));
const aria = (el) => clean(el && el.getAttribute("aria-label"));
const item = (id) => one(`button[data-item-id="${id}"]`) || one(`a[data-item-id="${id}"]`) || one(`[data-item-id="${id}"]`);
let name = txt(one("h1")) || clean(document.title.split(" - ")[0]);
const addressEl = item("address");
let address = aria(addressEl) || txt(addressEl);
address = clean(address.replace(/^Address:\s*/i, ""));
const phoneEl = one('button[data-item-id^="phone:tel:"]') || one('a[href^="tel:"]') || one('button[aria-label^="Phone:"]');
let phone = aria(phoneEl) || txt(phoneEl) || clean((phoneEl && phoneEl.getAttribute("href")) || "").replace(/^tel:/i, "");
phone = clean(phone.replace(/^Phone:\s*/i, ""));
const websiteEl = one('a[data-item-id="authority"]') || one('a[aria-label^="Website:"]') || one('a[data-item-id^="authority"]');
const website = websiteEl ? websiteEl.href : "";
const categoryEl = one('button[jsaction*="pane.rating.category"]') || one("button.DkEaL") || one('button[jsaction*="category"]');
const category = txt(categoryEl) || aria(categoryEl);
const ratingEl = one('div.F7nice span[aria-hidden="true"]') || one("span.ceNzKf") || one('span[aria-label*="stars"]');
let rating = txt(ratingEl) || aria(ratingEl);
const rm = rating.match(/[\d.]+/); rating = rm ? rm[0] : "";
const reviewEl = one('div.F7nice span[aria-label*="review"]') || one('button[aria-label*="review"]') || one('span[aria-label*="review"]');
let reviews = aria(reviewEl) || txt(reviewEl);
const rvm = String(reviews).match(/[\d,]+/); reviews = rvm ? rvm[0].replace(/,/g, "") : "";
const hoursEl = item("oh"); const hours = aria(hoursEl) || txt(hoursEl);
const plusEl = item("oloc"); const plusCode = aria(plusEl) || txt(plusEl);
const url = location.href;
const idm = url.match(/!1s([0-9a-fA-Fx]+:[0-9a-fA-Fx]+)/);
const cm = url.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
return {
  name, category, address, phone, website, rating, reviews, hours,
  plus_code: plusCode,
  place_url: url.split("&")[0],
  place_id: idm ? idm[1] : "",
  latitude: cm ? cm[1] : "",
  longitude: cm ? cm[2] : "",
  description: clean((one('[role="main"]') || document.body).innerText || "").slice(0, 900)
};
"""


def dismiss_consent(driver: webdriver.Firefox) -> None:
    for label in ("Accept all", "Reject all", "I agree", "Accept"):
        try:
            for button in driver.find_elements(By.XPATH, f"//button[contains(., '{label}')]"):
                if button.is_displayed():
                    button.click()
                    time.sleep(0.8)
                    return
        except Exception:
            continue


def wait_for_main(driver: webdriver.Firefox, timeout: float = 25.0) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="main"]'))
        )
    except TimeoutException:
        pass


def collect_links(driver: webdriver.Firefox, query: str, limit: int, max_scrolls: int) -> list[dict]:
    url = "https://www.google.com/maps/search/" + query.replace(" ", "+")
    driver.get(url)
    wait_for_main(driver)
    time.sleep(1.5)
    dismiss_consent(driver)
    time.sleep(1.0)

    feeds = driver.find_elements(By.CSS_SELECTOR, 'div[role="feed"]')
    if feeds:
        previous = 0
        stable = 0
        for _ in range(max_scrolls):
            count = len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/maps/place/"]'))
            if count >= limit:
                break
            if count == previous:
                stable += 1
            else:
                stable = 0
            if stable >= 3:
                break
            previous = count
            try:
                driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight)", feeds[0])
            except Exception:
                driver.execute_script("window.scrollBy(0, 2400)")
            time.sleep(1.1)

    cards = driver.execute_script(
        """
        return Array.from(document.querySelectorAll('a[href*="/maps/place/"]')).map(a => ({
          href: a.href,
          label: a.getAttribute('aria-label') || a.innerText || ''
        }));
        """
    )
    seen = set()
    out = []
    for card in cards or []:
        href = card.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(card)
        if len(out) >= limit:
            break
    if not out and "/maps/place/" in driver.current_url:
        out.append({"href": driver.current_url, "label": ""})
    return out


def extract_detail(driver: webdriver.Firefox) -> dict:
    return driver.execute_script(DETAIL_JS) or {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless Firefox Google Maps extractor.")
    parser.add_argument("--config", default=str(TMP_DIR / "maps-config.json"))
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    queries = cfg.get("queries") or []
    limit = int(cfg.get("limit_per_query") or 20)
    max_scrolls = int(cfg.get("max_scrolls") or 8)
    output = Path(cfg.get("output") or (TMP_DIR / "businesses.json"))
    if not queries:
        raise SystemExit("No queries in config.")

    options = Options()
    if args.headless:
        options.add_argument("-headless")
    options.add_argument("--width=1440")
    options.add_argument("--height=1000")
    options.set_preference(
        "general.useragent.override",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
    )

    driver = webdriver.Firefox(options=options)
    driver.set_page_load_timeout(45)
    all_rows: list[dict] = []
    seen: set[str] = set()
    report = []

    try:
        for query in queries:
            print(f"[maps-selenium] query: {query}")
            try:
                links = collect_links(driver, query, limit, max_scrolls)
            except WebDriverException as exc:
                print(f"[maps-selenium] query failed: {exc}")
                report.append({"query": query, "error": str(exc)[:300]})
                continue
            added = 0
            for card in links:
                try:
                    driver.get(card["href"])
                    wait_for_main(driver)
                    time.sleep(1.2)
                    dismiss_consent(driver)
                    detail = extract_detail(driver)
                    name = detail.get("name") or card.get("label", "")
                    if not name:
                        continue
                    key = detail.get("place_id") or (
                        name.strip().lower() + "|" + str(detail.get("address", "")).strip().lower()
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    detail["name"] = name
                    detail["query"] = query
                    detail["source_query"] = query
                    all_rows.append(detail)
                    added += 1
                except Exception as exc:
                    print(f"[maps-selenium] detail failed: {card.get('href')}: {exc}")
            report.append({"query": query, "found": len(links), "added": added})
            print(f"[maps-selenium] added {added}; total {len(all_rows)}")
    finally:
        driver.quit()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "queries": queries,
                "count": len(all_rows),
                "report": report,
                "businesses": all_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[maps-selenium] wrote {len(all_rows)} businesses -> {output}")


if __name__ == "__main__":
    main()
