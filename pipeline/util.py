import datetime as dt
import html
import random
import re
import time
from urllib.parse import urlparse, urlunparse

SKIP_HOSTS = ("google.", "bing.com", "microsoft.com", "msn.com", "youtube.com")
DROP_PARAMS = {"msockid", "utm_source", "utm_medium", "utm_campaign",
               "utm_term", "utm_content", "gclid", "fbclid", "srsltid", "aff"}


LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def nap(rng):
    time.sleep(random.uniform(*rng))


def now_s():
    return dt.datetime.now().isoformat(timespec="seconds")


def clean(s):
    """Text from HTML/JSON-LD: unescape, strip tags, collapse whitespace."""
    if s is None:
        return ""
    s = html.unescape(str(s))
    s = LONE_SURROGATE.sub("", s)  # half of a broken emoji escape; can't be saved
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tidy(url):
    """Drop tracking params; return None for search engines' own links."""
    if not url or not url.startswith("http"):
        return None
    p = urlparse(url)
    if any(h in p.netloc.lower() for h in SKIP_HOSTS):
        return None
    q = [kv for kv in p.query.split("&") if kv and kv.split("=")[0] not in DROP_PARAMS]
    return urlunparse(p._replace(query="&".join(q), fragment=""))


def domain(url):
    return urlparse(url).netloc.lower().removeprefix("www.")
