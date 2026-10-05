"""Pull event records out of one HTML page.

Three methods, best first:
  jsonld     schema.org Event objects embedded in the page. Most event sites
             (AllEvents, Meetup, Eventbrite, Luma, Townscript, StayHappening,
             WordPress event plugins) publish these, on listing pages too.
  links      the page has no events of its own -> collect links that look like
             individual event pages (known platforms, or /events/<slug> on the
             same site). The caller fetches those and runs this module again.
  heuristic  a page with no structured data that still looks like a single
             event: title + a date in the text (+ venue/time/price if labelled).
             Marked low confidence.

Records use the raw keys below; normalize.py turns them into output columns.
"""

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as dparser

from .util import clean

# --------------------------------------------------------------------------
# JSON-LD
# --------------------------------------------------------------------------

EVENT_TYPES = {"event", "businessevent", "educationevent", "socialevent", "hackathon",
               "exhibitionevent", "festival", "courseinstance", "eventseries",
               "literaryevent", "publicationevent", "screeningevent", "saleevent",
               "foodevent", "musicevent", "sportsevent", "theaterevent",
               "comedyevent", "danceevent", "childrensevent", "visualartsevent",
               "deliveryevent"}


def jsonld_objects(html):
    out = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                          html, re.S | re.I):
        raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):  # 2nd: drop trailing commas
            try:
                out.append(json.loads(attempt, strict=False))
                break
            except Exception:
                continue
    return out


def _is_event(obj):
    t = obj.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return any(str(x).lower().split("/")[-1] in EVENT_TYPES for x in types)


def find_events(obj, acc):
    if isinstance(obj, dict):
        if _is_event(obj) and obj.get("name"):
            acc.append(obj)
            return               # sub-events of a series stay inside it
        for v in obj.values():
            find_events(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            find_events(v, acc)


def _first(x):
    if isinstance(x, list):
        return x[0] if x else {}
    return x if x is not None else {}


def _text(x):
    """A value that may be a string, a {name|url|@id: ...} dict, or a list."""
    x = _first(x)
    if isinstance(x, dict):
        return clean(x.get("name") or x.get("url") or x.get("@id") or "")
    return clean(x)


def _price(offers):
    if isinstance(offers, dict):
        offers = [offers]
    if not isinstance(offers, list):
        return "", "", ""
    prices, currency, url = [], "", ""
    for o in offers:
        if not isinstance(o, dict):
            continue
        currency = currency or clean(o.get("priceCurrency"))
        url = url or clean(o.get("url"))
        for k in ("price", "lowPrice", "highPrice"):
            v = o.get(k)
            if v in (None, ""):
                continue
            try:
                prices.append(float(str(v).replace(",", "")))
            except ValueError:
                pass
    if prices:
        lo, hi = min(prices), max(prices)
        if hi == 0:
            return "Free", currency, url
        cur = "INR" if currency in ("", "INR", "Rs", "₹") else currency
        return (f"{cur} {lo:g}" if lo == hi else f"{cur} {lo:g}–{hi:g}"), currency, url
    return ("Free" if offers else ""), currency, url


def from_jsonld(e, page_url):
    loc = _first(e.get("location"))
    if isinstance(loc, str):
        loc = {"name": loc}
    online_url = ""
    if str(loc.get("@type", "")).lower() == "virtuallocation":
        online_url, loc = clean(loc.get("url")), {}
    addr = loc.get("address") or {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    if isinstance(addr, str):
        addr = {"streetAddress": addr}
    geo = loc.get("geo") or {}
    org = _first(e.get("organizer"))
    price, currency, ticket = _price(e.get("offers"))
    img = _first(e.get("image"))
    status = str(e.get("eventStatus", "")).split("/")[-1].replace("Event", "")

    parts = [clean(addr.get(k)) for k in
             ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
    return {
        "name": clean(e.get("name")),
        "description": clean(e.get("description")),
        "start": clean(e.get("startDate")),
        "end": clean(e.get("endDate")),
        "mode": clean(e.get("eventAttendanceMode")).split("/")[-1],
        "venue": clean(loc.get("name")),
        "street": parts[0],
        "locality": parts[1],
        "region": parts[2],
        "postal": parts[3],
        "country": _text(addr.get("addressCountry")),
        "address": ", ".join(p for p in parts if p),
        "lat": geo.get("latitude", "") if isinstance(geo, dict) else "",
        "lng": geo.get("longitude", "") if isinstance(geo, dict) else "",
        "online_url": online_url,
        "organizer": _text(org) if not isinstance(org, dict) else clean(org.get("name")),
        "organizer_url": clean(org.get("url")) if isinstance(org, dict) else "",
        "price": price,
        "currency": currency,
        "ticket_url": ticket,
        "event_url": urljoin(page_url, clean(e.get("url"))) if e.get("url") else page_url,
        "image": (clean(img.get("url")) if isinstance(img, dict) else clean(img)),
        "event_status": status,
        "method": "jsonld",
        "confidence": "high",
    }

# --------------------------------------------------------------------------
# links worth following from a page with no events of its own
# --------------------------------------------------------------------------

PLATFORM_LINKS = [re.compile(p, re.I) for p in (
    r"^https?://(?:www\.)?(?:lu\.ma|luma\.com)/(?!discover|signin|login|create|explore|home"
    r"|calendar|pricing|user|about|terms|privacy|ios|android)[\w-]{4,}/?$",
    r"^https?://(?:www\.)?meetup\.com/[^/]+/events/\d+",
    r"^https?://(?:www\.)?eventbrite\.[a-z.]+/e/[\w-]+",
    r"^https?://allevents\.in/[^/]+/[\w%-]+/\d{8,}",
    r"^https?://(?:www\.)?townscript\.com/e/[\w-]+",
    r"^https?://(?:www\.)?konfhub\.com/[\w-]{4,}/?$",
    r"^https?://unstop\.com/(?:events|hackathons|workshops|conferences|competitions)/[\w-]+",
    r"^https?://(?:www\.)?commudle\.com/communities/[^/]+/events/[\w-]+",
    r"^https?://in\.bookmyshow\.com/events/[\w-]+/ET\d+",
    r"^https?://(?:www\.)?district\.in/events/[\w-]+",
    r"^https?://(?:www\.)?startupgrind\.com/events/details/[\w-]+",
    r"^https?://(?:www\.)?stayhappening\.com/e/[\w-]+",
    r"^https?://[\w-]+\.devfolio\.co/?$",
    r"^https?://(?:www\.)?tickettailor\.com/events/[\w-]+/\d+",
)]

SAME_SITE_EVENT = re.compile(
    r"/(?:events?|programs?|workshops?|webinars?)/(?!(?:category|categories|tag|tags|page|list"
    r"|month|week|day|feed|search|upcoming|past|calendar|archive|ical)\b)[\w%-]{6,}/?$"
    r"|/events?/details/", re.I)


def event_links(soup, page_url):
    """Absolute URLs of likely single-event pages, platform links first."""
    here = urlparse(page_url)
    platform, same = [], []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip()).split("#")[0]
        p = urlparse(href)
        if p.scheme not in ("http", "https") or href.rstrip("/") == page_url.rstrip("/"):
            continue
        if any(rx.search(href) for rx in PLATFORM_LINKS):
            platform.append(href)
        elif p.netloc == here.netloc and SAME_SITE_EVENT.search(p.path):
            same.append(href)
    return list(dict.fromkeys(platform + same))

# --------------------------------------------------------------------------
# heuristic single-event page
# --------------------------------------------------------------------------

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_RX = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-\s*\d{{1,2}}(?:st|nd|rd|th)?\s*)?({_MON}),?\s*(\d{{4}})?\b"
    rf"|\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*-\s*\d{{1,2}})?,?\s*(\d{{4}})?\b"
    r"|\b(20\d\d)-(\d\d)-(\d\d)\b", re.I)
CLOCK = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", re.I)
LABEL = re.compile(r"^\s*(?:venue|location|where|address|place)\s*[:\-–]\s*(.{4,140})$", re.I | re.M)
EVENTISH = re.compile(r"\b(event|meetup|meet-up|summit|conference|workshop|hackathon|demo day"
                      r"|bootcamp|webinar|session|fest|festival|pitch|masterclass|networking"
                      r"|mixer|panel|talk|expo|conclave|startup weekend)\b", re.I)
LISTING_TITLE = re.compile(r"\bevents\b", re.I)
REGISTER = re.compile(r"\b(register|rsvp|book now|get tickets|buy tickets|apply now|sign up"
                      r"|join now|reserve)\b", re.I)


def _date_from_match(m, today):
    try:
        if m.group(7):
            return f"{m.group(7)}-{m.group(8)}-{m.group(9)}"
        if m.group(1):
            day, mon, year = m.group(1), m.group(2), m.group(3)
        else:
            mon, day, year = m.group(4), m.group(5), m.group(6)
        d = dparser.parse(f"{day} {mon} {year or today.year}", dayfirst=True).date()
        if not year:   # no year printed: pick the one that lands nearest today
            for y in (today.year - 1, today.year + 1):
                alt = d.replace(year=y)
                if abs((alt - today).days) < abs((d - today).days):
                    d = alt
        return d.isoformat()
    except Exception:
        return ""


def heuristic(soup, page_url, today):
    def meta(prop):
        t = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return clean(t.get("content")) if t and t.get("content") else ""

    h1 = soup.find("h1")
    title = meta("og:title") or (clean(h1.get_text(" ")) if h1 else "") or \
        clean(soup.title.get_text(" ") if soup.title else "")
    if not title or not (EVENTISH.search(title) or SAME_SITE_EVENT.search(urlparse(page_url).path)):
        return None

    main = soup.find("main") or soup.find("article") or soup.body or soup
    for t in main.find_all(["script", "style", "noscript", "nav", "footer", "header"]):
        t.decompose()
    lines = "\n".join(clean(x) for x in main.get_text("\n").split("\n") if clean(x))
    head = lines[:5000]

    # listing pages ("Upcoming Events in Kolkata", "Signature Events") are not
    # one event: a plural "events" title, or many different dates on the page
    if LISTING_TITLE.search(title) and not EVENTISH.search(title):
        return None
    matches = list(DATE_RX.finditer(head))
    if not matches or len({_date_from_match(x, today) for x in matches} - {""}) >= 4:
        return None
    m = matches[0]
    start = _date_from_match(m, today)
    if not start:
        return None
    clock = CLOCK.findall(head[max(0, m.start() - 200): m.end() + 300])
    venue = LABEL.search(head)
    low = head.lower()
    price = "Free" if re.search(r"\bfree\b", low) else ""
    if not price:
        p = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]{2,9})", low)
        price = f"INR {p.group(1)}" if p else ""
    reg = ""
    for a in soup.find_all("a", href=True):
        if REGISTER.search(a.get_text(" ")):
            reg = urljoin(page_url, a["href"])
            break

    return {
        "name": title, "description": meta("og:description") or meta("description"),
        "start": start + (f" {clock[0]}" if clock else ""),
        "end": f"{start} {clock[1]}" if len(clock) > 1 else "",
        "mode": "", "venue": clean(venue.group(1)) if venue else "",
        "street": "", "locality": "", "region": "", "postal": "", "country": "",
        "address": clean(venue.group(1)) if venue else "", "lat": "", "lng": "",
        "online_url": "", "organizer": meta("og:site_name"), "organizer_url": "",
        "price": price, "currency": "", "ticket_url": reg, "event_url": page_url,
        "image": meta("og:image"), "event_status": "",
        "method": "heuristic", "confidence": "low",
    }

# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse(html, page_url, today, allow_heuristic=True):
    """Returns (events, links_to_follow). Links only when no JSON-LD events."""
    acc = []
    for obj in jsonld_objects(html):
        find_events(obj, acc)
    events = [from_jsonld(e, page_url) for e in acc]
    events = [e for e in events if e["name"]]
    if events:
        return events, []

    soup = BeautifulSoup(html, "lxml")
    links = event_links(soup, page_url)
    if allow_heuristic:
        h = heuristic(soup, page_url, today)
        if h:
            return [h], links
    return [], links
