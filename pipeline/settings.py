"""Paths and config.json, loaded once."""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "output")
DEBUG_DIR = os.path.join(OUT_DIR, "debug")
DB_PATH = os.path.join(OUT_DIR, "pipeline.db")
EVENTS_XLSX = os.path.join(OUT_DIR, "events.xlsx")
EVENTS_CSV = os.path.join(OUT_DIR, "events.csv")
LINKS_CSV = os.path.join(OUT_DIR, "search_results.csv")
PROFILE_DIR = os.path.join(ROOT, ".chrome_profile")

with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as _f:
    CFG = json.load(_f)

TEMPLATES = CFG["templates"]
CITIES = CFG["cities"]            # query name -> Google canonical location
PAGES = CFG.get("pages", 3)       # 10 results per page

_X = CFG.get("extract", {})
FOLLOW_LINKS = _X.get("follow_links_per_page", 15)
KEEP_OUTSIDE_INDIA = _X.get("keep_outside_india", False)
DESC_CHARS = _X.get("description_chars", 300)
DOMAIN_GAP = _X.get("seconds_between_hits_same_site", 2.0)
