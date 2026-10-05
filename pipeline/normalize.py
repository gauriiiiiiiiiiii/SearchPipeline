"""Raw extracted records -> clean output rows.

Adds: parsed dates, Upcoming/Ongoing/Past, Online/Offline, India check, city,
event type, sector, keyword tags, startup relevance. Then merges duplicates
(the same event found on several pages / platforms).

Classification vocab is shared with ../scrape_events.py.
"""

import datetime as dt
import re
from collections import OrderedDict

from dateutil import parser as dparser

from .settings import DESC_CHARS
from .util import clean, domain

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# --------------------------------------------------------------------------
# vocab
# --------------------------------------------------------------------------

STARTUP_KEYWORDS = [
    "startup", "start-up", "founder", "entrepreneur", "venture", "vc ", "angel",
    "investor", "funding", "fundrais", "pitch", "demo day", "incubat", "accelerat",
    "unicorn", "saas", "d2c", "product management", "growth hacking", "bootstrap",
    "co-founder", "cofounder", "ecosystem", "innovation", "hackathon", "builder",
    "indie hacker", "solopreneur", "business network", "pre-seed", "seed round",
    "gtm", "go-to-market", "mvp", "y combinator", "shark tank", "tech meetup",
    "developer meetup", "no-code", "msme", "coworking", "co-working",
]

EVENT_TYPES = [
    ("Hackathon",       ["hackathon", "hack ", "codefest", "buildathon"]),
    ("Demo Day",        ["demo day", "showcase", "demoday"]),
    ("Pitch / Funding", ["pitch", "funding", "investor connect", "fundraise", "term sheet"]),
    ("Conference",      ["conference", "summit", "expo", "convention", "conclave"]),
    ("Workshop",        ["workshop", "bootcamp", "masterclass", "training", "hands-on",
                         "cohort", "course"]),
    ("Webinar",         ["webinar", "online session", "live session", "ama", "fireside"]),
    ("Panel",           ["panel", "roundtable", "round table", "discussion"]),
    ("Competition",     ["competition", "contest", "challenge", "awards"]),
    ("Networking",      ["networking", "mixer", "connect", "social", "mingle", "coffee",
                         "brunch", "dinner", "collab"]),
    ("Meetup",          ["meetup", "meet up", "meet-up", "gathering", "huddle", "circle",
                         "open house", "community"]),
]

SECTORS = [
    ("AI / ML",        ["ai ", " ai", "artificial intelligence", "machine learning", "llm",
                        "genai", "gen ai", "agents", "deep learning", "nlp", "computer vision"]),
    ("Web3 / Crypto",  ["web3", "blockchain", "crypto", "nft", "defi", "ethereum", "solana"]),
    ("Fintech",        ["fintech", "payments", "banking", "lending", "insurtech", "upi"]),
    ("D2C / Consumer", ["d2c", "dtc", "consumer", "ecommerce", "e-commerce", "retail", "brand"]),
    ("SaaS / B2B",     ["saas", "b2b", "enterprise", "crm", "devtools", "developer tool"]),
    ("HealthTech",     ["health", "medtech", "biotech", "wellness", "pharma"]),
    ("EdTech",         ["edtech", "education", "learning", "student", "campus"]),
    ("Climate",        ["climate", "sustainab", "green", "cleantech", "ev ", "solar", "energy"]),
    ("DeepTech",       ["deeptech", "deep tech", "robotics", "space", "semiconductor", "drone"]),
    ("Gaming / Media", ["gaming", "game", "esports", "media", "creator"]),
]

KEYWORD_VOCAB = [
    ("founders",   ["founder", "co-founder", "cofounder", "entrepreneur"]),
    ("investors",  ["investor", "vc ", "venture capital", "angel"]),
    ("funding",    ["funding", "fundrais", "investment", "seed", "series a"]),
    ("pitching",   ["pitch"]),
    ("demo-day",   ["demo day", "showcase"]),
    ("networking", ["network", "mixer", "mingle", "connect"]),
    ("hackathon",  ["hackathon", "buildathon"]),
    ("workshop",   ["workshop", "bootcamp", "masterclass", "training"]),
    ("panel",      ["panel", "fireside", "roundtable", "keynote"]),
    ("conference", ["conference", "summit", "expo"]),
    ("AI",         ["ai ", " ai", "artificial intelligence", "machine learning", "llm", "genai"]),
    ("SaaS",       ["saas", "b2b"]),
    ("fintech",    ["fintech", "payments"]),
    ("marketing",  ["marketing", "growth", "gtm", "branding"]),
    ("product",    ["product management", "product manager"]),
    ("developers", ["developer", "engineer", "coding", "devops"]),
    ("women",      ["women", "female founder"]),
    ("students",   ["student", "campus", "college", "university"]),
    ("coworking",  ["coworking", "co-working", "cowork"]),
    ("incubation", ["incubat", "accelerat"]),
    ("mentorship", ["mentor", "office hours"]),
    ("community",  ["community", "meetup", "open house"]),
]

# Title-only noise filters (see ../scrape_events.py for why they're title-only)
EXCLUDE = [
    "property expo", "property show", "real estate", "realty", "residential plot",
    "new launches in", "job fair", "placement drive", "walk-in interview",
    "astrolog", "tarot", "numerolog", "vastu", "matrimon", "forex signal",
    "binary option", "network marketing", "weight loss",
]

# Indian city names / aliases -> the name used in output
CITY_ALIASES = {
    "new delhi": "Delhi", "delhi": "Delhi", "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "noida": "Noida", "greater noida": "Noida", "ghaziabad": "Ghaziabad",
    "faridabad": "Faridabad", "mumbai": "Mumbai", "bombay": "Mumbai",
    "navi mumbai": "Navi Mumbai", "thane": "Thane", "pune": "Pune",
    "bengaluru": "Bangalore", "bangalore": "Bangalore", "hyderabad": "Hyderabad",
    "secunderabad": "Hyderabad", "chennai": "Chennai", "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad", "gandhinagar": "Gandhinagar", "surat": "Surat",
    "vadodara": "Vadodara", "jaipur": "Jaipur", "chandigarh": "Chandigarh",
    "mohali": "Mohali", "kochi": "Kochi", "cochin": "Kochi",
    "thiruvananthapuram": "Thiruvananthapuram", "trivandrum": "Thiruvananthapuram",
    "indore": "Indore", "bhopal": "Bhopal", "lucknow": "Lucknow", "kanpur": "Kanpur",
    "nagpur": "Nagpur", "nashik": "Nashik", "coimbatore": "Coimbatore",
    "madurai": "Madurai", "mysuru": "Mysuru", "mysore": "Mysuru",
    "mangaluru": "Mangaluru", "mangalore": "Mangaluru", "visakhapatnam": "Visakhapatnam",
    "vizag": "Visakhapatnam", "vijayawada": "Vijayawada", "bhubaneswar": "Bhubaneswar",
    "patna": "Patna", "ranchi": "Ranchi", "guwahati": "Guwahati", "dehradun": "Dehradun",
    "goa": "Goa", "panaji": "Goa", "raipur": "Raipur", "ludhiana": "Ludhiana",
    "amritsar": "Amritsar", "varanasi": "Varanasi", "udaipur": "Udaipur",
    "jodhpur": "Jodhpur", "kozhikode": "Kozhikode", "calicut": "Kozhikode",
    "hubli": "Hubballi", "trichy": "Tiruchirappalli", "tiruchirappalli": "Tiruchirappalli",
}
_CITY_RX = re.compile(r"\b(" + "|".join(sorted(map(re.escape, CITY_ALIASES), key=len,
                                                reverse=True)) + r")\b", re.I)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _blob(*parts):
    return " ".join(clean(p).lower() for p in parts if p)


def classify(pairs, *parts, default=""):
    b = _blob(*parts)
    for label, keys in pairs:
        if any(k in b for k in keys):
            return label
    return default


def tags(*parts, limit=5):
    b = _blob(*parts)
    return ", ".join([lbl for lbl, keys in KEYWORD_VOCAB if any(k in b for k in keys)][:limit])


def parse_dt(s):
    """ISO or free text -> naive IST datetime, or None."""
    if not s:
        return None
    try:
        d = dparser.isoparse(s)
    except Exception:
        try:
            d = dparser.parse(s, dayfirst=True, fuzzy=True)
        except Exception:
            return None
    if d.tzinfo:
        d = d.astimezone(IST).replace(tzinfo=None)
    return d


def has_time(s):
    return bool(re.search(r"T\d|\d{1,2}:\d{2}|\d\s*(?:am|pm)", s or "", re.I))


def find_city(*parts):
    m = _CITY_RX.search(_blob(*parts))
    return CITY_ALIASES[m.group(1).lower()] if m else ""


def location_check(r, city):
    country = r["country"].strip().lower()
    if country in ("in", "ind", "india"):
        return "India"
    if city:
        return "India"
    if "india" in _blob(r["address"], r["venue"], r["region"]):
        return "India"
    if r["format"] == "Online":
        return "Online"
    if country:
        return "Outside India"
    return "Unknown"

# --------------------------------------------------------------------------
# one record
# --------------------------------------------------------------------------


def row(raw, now):
    r = dict(raw)
    start, end = parse_dt(r["start"]), parse_dt(r["end"])
    st_time, en_time = has_time(r["start"]), has_time(r["end"])

    mode = r["mode"].lower()
    r["format"] = ("Online" if "online" in mode else "Hybrid" if "mixed" in mode else
                   "Offline" if "offline" in mode else "")
    if not r["format"]:
        if r["online_url"] or re.search(r"\b(online|virtual|zoom|google meet|webinar)\b",
                                        _blob(r["venue"], r["name"])):
            r["format"] = "Online"
        elif r["venue"] or r["address"]:
            r["format"] = "Offline"

    # most specific first: "Garden Galleria: Noida" beats a locality of "New Delhi"
    city = (find_city(r["venue"], r["street"]) or find_city(r["locality"]) or
            find_city(r["address"], r["region"]) or find_city(r["name"]))
    loc = location_check(r, city)

    if start is None:
        status = "Date unknown"
    else:
        finish = end or (start + dt.timedelta(hours=23 if not st_time else 4))
        status = ("Past" if finish < now else "Ongoing" if start <= now else "Upcoming")

    name, desc = r["name"], r["description"]
    etype = classify(EVENT_TYPES, name, desc, default="Meetup")
    return {
        "Status": status,
        "Event Name": name,
        "Start Date": start.strftime("%Y-%m-%d") if start else "",
        "Day": start.strftime("%A") if start else "",
        "Start Time": start.strftime("%I:%M %p").lstrip("0") if start and st_time else "",
        "End Date": end.strftime("%Y-%m-%d") if end else "",
        "End Time": end.strftime("%I:%M %p").lstrip("0") if end and en_time else "",
        "Duration (hrs)": (round((end - start).total_seconds() / 3600, 1)
                           if start and end and end > start and st_time and en_time else ""),
        "Event Format": r["format"],
        "Venue": r["venue"],
        "Area / Locality": r["street"],
        "City": city,
        "State": r["region"],
        "Country": r["country"],
        "Full Address": r["address"],
        "Latitude": r["lat"],
        "Longitude": r["lng"],
        "Location Check": loc,
        "Hosted By": r["organizer"],
        "Organizer Link": r["organizer_url"],
        "Event Type": etype,
        "Industry / Sector Focus": classify(SECTORS, name, desc),
        "Keywords": tags(name, desc) or etype.lower(),
        "Startup Relevant": "Yes" if any(k in _blob(name, desc, r["organizer"])
                                         for k in STARTUP_KEYWORDS) else "No",
        "Price": r["price"],
        "Registration Link": r["ticket_url"] or r["online_url"],
        "Event Link": r["event_url"],
        "Event Status": r["event_status"],
        "Description": (desc[:DESC_CHARS] + "…") if len(desc) > DESC_CHARS else desc,
        "Image": r["image"],
        "Source Platform": domain(r["event_url"]) if r["event_url"] else "",
        "Found On": r.get("found_on", ""),
        "Search Queries": r.get("queries", ""),
        "Search Cities": r.get("search_cities", ""),
        "Extraction Method": r["method"],
        "Confidence": r["confidence"],
    }


COLUMNS = list(row({k: "" for k in (
    "name description start end mode venue street locality region postal country address "
    "lat lng online_url organizer organizer_url price currency ticket_url event_url image "
    "event_status method confidence").split()}, dt.datetime.now()).keys())

# --------------------------------------------------------------------------
# filter + merge
# --------------------------------------------------------------------------

MULTI = ("Found On", "Search Queries", "Search Cities", "Source Platform")


def _url_key(u):
    u = re.sub(r"^https?://(www\.)?", "", (u or "").lower()).split("?")[0].rstrip("/")
    return u.replace("luma.com/", "lu.ma/")


def _name_key(r):
    return (re.sub(r"[^a-z0-9]", "", r["Event Name"].lower())[:40], r["Start Date"])


def merge(rows, keep_outside_india=False):
    """Drop noise and (optionally) non-India events; merge duplicates."""
    stats = {"noise": 0, "outside_india": 0, "duplicates": 0}
    kept = []
    for r in rows:
        if any(k in r["Event Name"].lower() for k in EXCLUDE):
            stats["noise"] += 1
            continue
        if r["Location Check"] == "Outside India" and not keep_outside_india:
            stats["outside_india"] += 1
            continue
        kept.append(r)

    # prefer the richest record as the base of each merged group
    kept.sort(key=lambda r: (r["Confidence"] != "high", -sum(bool(str(v)) for v in r.values())))
    by_key, out = {}, []
    for r in kept:
        keys = [k for k in (("u", _url_key(r["Event Link"])), ("n",) + _name_key(r)) if k[1]]
        base = next((by_key[k] for k in keys if k in by_key), None)
        if base is None:
            r = dict(r)
            for c in MULTI:
                r[c] = OrderedDict.fromkeys(x for x in str(r[c]).split(" | ") if x)
            out.append(r)
            base = r
        else:
            stats["duplicates"] += 1
            for c, v in r.items():
                if c in MULTI:
                    base[c].update(OrderedDict.fromkeys(x for x in str(v).split(" | ") if x))
                elif not str(base.get(c, "")).strip() and str(v).strip():
                    base[c] = v
        for k in keys:
            by_key.setdefault(k, base)

    for r in out:
        for c in MULTI:
            r[c] = " | ".join(r[c])
    # soonest upcoming first; most recent past first
    by = lambda s: [r for r in out if r["Status"] == s]
    soon = lambda r: (r["Start Date"], r["City"])
    out = (sorted(by("Ongoing"), key=soon) + sorted(by("Upcoming"), key=soon)
           + sorted(by("Past"), key=soon, reverse=True) + by("Date unknown"))
    return out, stats
