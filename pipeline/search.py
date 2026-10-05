"""Stage 1: run the queries on a search engine and store the result links.

Engines:
    bing    (default) real result URLs, India market (cc=IN, mkt=en-IN).
            Tested Oct 2026: no blocks at this pace.
    google  experimental. As of Oct 2026 Google hides result URLs behind an
            encrypted /goto?url= redirect, and 403-bans an IP after only a
            handful of automated queries. Each link is resolved with one
            extra request to the redirect. Use sparingly.
"""

import base64
import datetime as dt
import os
import random
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .browser import body_text, launch
from .settings import CITIES, DEBUG_DIR, TEMPLATES
from .util import domain, log, nap, now_s, tidy

GOOGLE_CR = False         # True adds cr=countryIN: only pages Google tags as
                          # Indian (.in / Indian-hosted). It drops lu.ma,
                          # meetup.com, eventbrite.com etc., so it is off;
                          # gl=in + uule + the city in the query keep it India.

DELAY = (15, 35)          # seconds between page loads, random in this range
BREAK_EVERY = 10          # after this many queries...
BREAK_LEN = (90, 240)     # ...take a longer pause (seconds)
CAPTCHA_WAIT = 15 * 60    # how long to wait for you to solve a CAPTCHA

# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------


class Bing:
    name = "bing"

    def url(self, query, city_canon, page):
        params = {"q": query, "cc": "IN", "mkt": "en-IN", "setlang": "en-IN"}
        if page:
            params["first"] = page * 10 + 1
        return "https://www.bing.com/search?" + urlencode(params)

    def captcha(self, page):
        return (page.locator("iframe[src*='challenges.cloudflare.com'], #b_captcha").count() > 0
                or "solve the challenge" in body_text(page).lower())

    def banned(self, page):
        return False

    @staticmethod
    def _unwrap(href):
        # bing.com/ck/a?...&u=a1<base64 url>
        if "bing.com/ck/a" in href:
            u = parse_qs(urlparse(href).query).get("u", [""])[0]
            if u.startswith("a1"):
                s = u[2:]
                try:
                    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()
                except Exception:
                    return None
        return href

    def results(self, page):
        page.wait_for_selector("#b_results", timeout=15000)
        items = page.eval_on_selector_all(
            "#b_results li.b_algo h2 a",
            "els => els.map(a => [a.href, a.innerText])")
        out = [(t, self._unwrap(h)) for h, t in items]
        return out, page.locator("a.sb_pagN").count() > 0


class Google:
    name = "google"
    _KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

    def _uule(self, canonical):
        raw = canonical.encode()
        return "w+CAIQICI" + self._KEY[len(raw)] + base64.b64encode(raw).decode()

    def url(self, query, city_canon, page):
        params = {"q": query, "hl": "en", "gl": "in", "pws": "0"}
        if GOOGLE_CR:
            params["cr"] = "countryIN"
        if page:
            params["start"] = page * 10
        url = "https://www.google.co.in/search?" + urlencode(params)
        # uule must not be percent-encoded, so append it by hand
        return url + ("&uule=" + self._uule(city_canon) if city_canon else "")

    def captcha(self, page):
        return ("/sorry/" in page.url
                or page.locator("iframe[src*='recaptcha'], form#captcha-form").count() > 0)

    def banned(self, page):
        return "does not have permission" in body_text(page)

    def _resolve(self, page, href):
        """Follow /goto?url= or /url?q= to the real destination."""
        if href.startswith("/url?"):
            return parse_qs(urlparse(href).query).get("q", [None])[0]
        if not href.startswith("/goto?"):
            return href
        try:
            r = page.request.get(urljoin(page.url, href), max_redirects=0)
            time.sleep(random.uniform(0.8, 2.0))
            return r.headers.get("location")
        except Exception:
            return None

    def results(self, page):
        page.wait_for_selector("#rso h3", timeout=20000)
        items = page.eval_on_selector_all(
            "#rso a:has(h3)",
            "els => els.map(a => [a.getAttribute('href'), a.querySelector('h3').innerText])")
        out = [(t, self._resolve(page, h)) for h, t in items]
        return out, page.locator("a#pnnext").count() > 0


ENGINES = {"bing": Bing(), "google": Google()}

# --------------------------------------------------------------------------
# page handling
# --------------------------------------------------------------------------

SKIP = "skip"


def wait_out_captcha(engine, page, headless):
    if headless:
        log("CAPTCHA hit in headless mode -- stopping. Re-run without --headless "
            "and solve it in the window, or wait an hour and retry.")
        return False
    log(">>> CAPTCHA shown. Solve it in the Chrome window. "
        f"Waiting up to {CAPTCHA_WAIT // 60} min...")
    print("\a", end="", flush=True)
    deadline = time.time() + CAPTCHA_WAIT
    while time.time() < deadline:
        time.sleep(3)
        if not engine.captcha(page):
            log("CAPTCHA cleared, continuing.")
            nap((5, 10))
            return True
    log("CAPTCHA not solved in time -- stopping. Progress is saved.")
    return False


def accept_consent(page):
    for label in ("Accept all", "Accept", "I agree"):
        btn = page.get_by_role("button", name=label, exact=True)
        if btn.count():
            btn.first.click()
            page.wait_for_load_state("domcontentloaded")
            return


def human_scroll(page):
    for _ in range(random.randint(2, 4)):
        page.mouse.wheel(0, random.randint(300, 700))
        time.sleep(random.uniform(0.4, 1.2))


def looks_degraded(raw, city, canon, last_urls):
    """Bing sometimes soft-throttles by ignoring most of the query and serving
    generic results (seen: 'upcoming movies' for '"upcoming events" coworking
    Pune'). Flag a page where no result mentions the city, or that repeats the
    previous query's results exactly."""
    if not raw:
        return False
    names = {city.lower(), canon.split(",")[0].lower()} if canon else {city.lower()}
    hits = sum(any(n in f"{t} {u}".lower() for n in names) for t, u in raw)
    return hits == 0 or {u for _, u in raw} == last_urls


def fetch_serp(engine, page, url, headless):
    """Load one results page. Returns (results, has_next), SKIP, or None to stop."""
    for attempt in range(4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            break
        except Exception as e:
            if attempt == 3:
                log(f"network keeps failing ({str(e).splitlines()[0]}) -- stopping. "
                    "Progress is saved.")
                return None
            log(f"    network error, retrying in 30s ({str(e).splitlines()[0][:60]})")
            time.sleep(30)
    accept_consent(page)
    if engine.banned(page):
        log(f"{engine.name} returned 403 (IP temporarily banned). Progress is saved -- "
            "retry in a few hours, or use another engine.")
        return None
    if engine.captcha(page):
        if not wait_out_captcha(engine, page, headless):
            return None
        page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(1500, 3000))
    human_scroll(page)
    try:
        return engine.results(page)
    except Exception as e:
        if engine.captcha(page) or engine.banned(page):
            log("blocked while reading results -- stopping. Progress is saved.")
            return None
        # unknown page (challenge, layout change, blank load): keep a
        # screenshot to look at, and don't cache it so the next run retries
        os.makedirs(DEBUG_DIR, exist_ok=True)
        shot = os.path.join(DEBUG_DIR, f"{engine.name}_{dt.datetime.now():%Y%m%d_%H%M%S}.png")
        page.screenshot(path=shot)
        log(f"    no results block found ({type(e).__name__}); screenshot -> {shot}")
        return SKIP

# --------------------------------------------------------------------------
# stage entry point
# --------------------------------------------------------------------------


def query_finished(db, engine, query, pages):
    """True when every page was fetched, or the engine ran out of results."""
    rows = db.execute("SELECT has_next FROM done WHERE engine=? AND query=?",
                      (engine, query)).fetchall()
    return any(not h for (h,) in rows) or len(rows) >= pages


def pick_cities(names):
    if not names:
        return CITIES
    wanted = [c.strip() for c in names.split(",") if c.strip()]
    return {c: CITIES.get(c, f"{c},India") for c in wanted}


def run(db, engine_name="bing", cities=None, pages=3, limit=None, headless=False):
    engine = ENGINES[engine_name]
    jobs = [(city, canon, t, t.format(city=city))
            for city, canon in pick_cities(cities).items() for t in TEMPLATES]
    pending = [j for j in jobs if not query_finished(db, engine.name, j[3], pages)]
    todo = pending[:limit] if limit else pending
    log(f"[search:{engine.name}] {len(jobs)} queries, {len(jobs) - len(pending)} already "
        f"cached, {len(todo)} to run now")
    if not todo:
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx, page = launch(pw, headless)
        last_urls = set()
        try:
            for qi, (city, canon, tmpl, query) in enumerate(todo, 1):
                if qi > 1 and (qi - 1) % BREAK_EVERY == 0:
                    log("taking a longer break...")
                    nap(BREAK_LEN)
                log(f"({qi}/{len(todo)}) {query}")

                for p in range(pages):
                    if db.execute("SELECT 1 FROM done WHERE engine=? AND query=? AND page=?",
                                  (engine.name, query, p)).fetchone():
                        continue
                    for attempt in range(3):
                        got = fetch_serp(engine, page, engine.url(query, canon, p), headless)
                        if got is None:
                            return
                        if got == SKIP:
                            break
                        raw, has_next = got
                        if p or not looks_degraded(raw, city, canon, last_urls):
                            break
                        if attempt == 2:
                            continue
                        log("    results look unrelated to the query (soft throttle?) "
                            f"-- pausing, retry {attempt + 1}/2")
                        nap((60, 120))
                    else:
                        log("    still unrelated -- skipping; it will be retried next run")
                        break
                    if got == SKIP:
                        log("    skipping this query; it will be retried next run")
                        nap(DELAY)
                        break
                    if p == 0:
                        last_urls = {u for _, u in raw}

                    ts = now_s()
                    seen, rank = set(), 0
                    for title, url in raw:
                        url = tidy(url)
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        rank += 1
                        db.execute("INSERT OR IGNORE INTO results VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (engine.name, city, tmpl, query, p + 1, p * 10 + rank,
                                    title.strip(), url, domain(url), ts))
                    db.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?,?,?)",
                               (engine.name, query, p, rank, int(has_next), ts))
                    db.commit()
                    log(f"    page {p + 1}: {rank} links")

                    nap(DELAY)
                    if not has_next:
                        break
        except KeyboardInterrupt:
            log("stopped by you -- progress is saved, re-run to resume.")
            raise
        finally:
            try:
                ctx.close()
            except Exception:   # window already closed by hand
                pass
