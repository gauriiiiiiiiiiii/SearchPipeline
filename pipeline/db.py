"""SQLite store. Every stage writes as it goes, so any run can be resumed."""

import json
import os
import sqlite3

from .settings import DB_PATH, OUT_DIR
from .util import LONE_SURROGATE

SCHEMA = """
-- stage 1: search
CREATE TABLE IF NOT EXISTS done (
    engine TEXT, query TEXT, page INTEGER, n INTEGER, has_next INTEGER, ts TEXT,
    PRIMARY KEY (engine, query, page));
CREATE TABLE IF NOT EXISTS results (
    engine TEXT, city TEXT, template TEXT, query TEXT, page INTEGER,
    rank INTEGER, title TEXT, url TEXT, domain TEXT, ts TEXT,
    UNIQUE (engine, query, url));

-- stage 2: extract. One row per fetched page (search result or a link
-- followed from one); events found on it are in events.
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY, parent TEXT, status TEXT, via TEXT, http INTEGER,
    n_events INTEGER, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, page_url TEXT, data TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS events_page ON events (page_url);
"""


def dumps(record):
    """JSON for storage; any stray surrogate that slipped past clean() is dropped."""
    return LONE_SURROGATE.sub("", json.dumps(record, ensure_ascii=False))


def connect():
    os.makedirs(OUT_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    return db


def save_page(db, url, parent, status, via, http, events, note, ts):
    db.execute("DELETE FROM events WHERE page_url=?", (url,))
    for e in events:
        db.execute("INSERT INTO events (page_url, data, ts) VALUES (?,?,?)",
                   (url, dumps(e), ts))
    db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?,?)",
               (url, parent, status, via, http, len(events), note, ts))
    db.commit()
