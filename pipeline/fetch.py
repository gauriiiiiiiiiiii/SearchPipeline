"""Fetch a web page: plain HTTP first (fast), real Chrome when the site blocks
bots (403/Cloudflare) or only renders with JavaScript."""

import re
import time

import truststore  # trust the Windows cert store (corp TLS proxy in this env)
truststore.inject_into_ssl()

import requests

from .settings import DOMAIN_GAP
from .util import domain

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

HTTP_DEADLINE = 40          # seconds for one whole HTTP download
MAX_BYTES = 5_000_000


class NotHtml(Exception):
    pass


CHALLENGE = ("just a moment...", "cf-chl", "attention required", "verify you are human",
             "enable javascript and cookies", "access denied")


def visible_len(html):
    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip())


def needs_browser(status, html):
    if status in (401, 403, 429, 503):
        return True
    if status != 200 or not html:
        return False
    head = html[:6000].lower()
    if any(c in head for c in CHALLENGE):
        return True
    # JS shell: almost no text and no structured data to fall back on
    return visible_len(html) < 800 and "application/ld+json" not in html


class Fetcher:
    """Use as a context manager; Chrome is only started if a page needs it."""

    def __init__(self, use_browser=True):
        self.use_browser = use_browser
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._last = {}
        self._pw = self._ctx = self._page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        # the window may already be gone (closed by hand, or Chrome crashed)
        for close in (self._ctx and self._ctx.close, self._pw and self._pw.stop):
            if close:
                try:
                    close()
                except Exception:
                    pass

    def _polite(self, url):
        d = domain(url)
        wait = self._last.get(d, 0) + DOMAIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last[d] = time.time()

    def _browser(self):
        if not self._page:
            from playwright.sync_api import sync_playwright
            from .browser import launch
            self._pw = sync_playwright().start()
            self._ctx, self._page = launch(self._pw)
        return self._page

    def _http(self, url):
        """GET with a hard overall deadline. requests' own timeout is per socket
        read, so a server that trickles bytes can hold one call for half an
        hour (seen: 1904 s) -- stream and stop at HTTP_DEADLINE / MAX_BYTES."""
        with self.session.get(url, timeout=(10, 20), stream=True) as r:
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype:
                raise NotHtml(f"not a web page ({ctype[:40]})")
            deadline, buf = time.time() + HTTP_DEADLINE, bytearray()
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) > MAX_BYTES or time.time() > deadline:
                    break
            # requests assumes ISO-8859-1 when the header has no charset; most
            # of these pages are really UTF-8
            enc = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else None
            try:
                text = buf.decode(enc or "utf-8")
            except (UnicodeDecodeError, LookupError):
                text = buf.decode(enc or "latin-1", errors="replace")
            return text, r.status_code, r.url

    def get(self, url):
        """Returns (html, final_url, via, http_status, note)."""
        self._polite(url)
        status, html, final, note = 0, "", url, ""
        try:
            html, status, final = self._http(url)
        except Exception as e:
            note = f"{type(e).__name__}"
            if isinstance(e, NotHtml):
                return "", final, "http", 200, str(e)

        if not (self.use_browser and (note or needs_browser(status, html))):
            return html, final, "http", status, note

        try:
            page = self._browser()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            # lazy lists: scroll once so cards below the fold render
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1500)
            return (page.content(), page.url, "browser",
                    resp.status if resp else 0, "")
        except Exception as e:
            return html, final, "http", status, f"browser failed: {str(e).splitlines()[0][:80]}"
