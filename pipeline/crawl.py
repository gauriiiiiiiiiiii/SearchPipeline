"""Stage 2: visit every search-result link and extract the events on it.

Per result page:
    fetch (HTTP, or Chrome if blocked / JS-only)
      -> JSON-LD events found?        save them, done
      -> else event links on page?    fetch up to FOLLOW_LINKS of them and
                                      extract each (JSON-LD, else heuristic)
      -> else / nothing from links:   heuristic on the page itself
Every page is saved as it finishes, so the stage resumes where it stopped.
"""

import datetime as dt
import json

from . import extract
from .db import dumps, save_page
from .fetch import Fetcher
from .settings import FOLLOW_LINKS
from .util import log, now_s


def _process(fetcher, url, today, allow_heuristic=True):
    html, final, via, http, note = fetcher.get(url)
    if not html:
        return None, [], via, http, note or f"HTTP {http}"
    events, links = extract.parse(html, final, today, allow_heuristic)
    return events, links, via, http, note


def run(db, limit=None, retry_errors=False, use_browser=True):
    today = dt.date.today()
    results = [u for (u,) in db.execute(
        "SELECT url FROM results GROUP BY url ORDER BY MIN(rank)")]
    skip_status = ("events", "followed", "heuristic", "none") + (() if retry_errors else ("error",))
    seen = {u for (u,) in db.execute(
        f"SELECT url FROM pages WHERE status IN ({','.join('?' * len(skip_status))})",
        skip_status)}
    pending = [u for u in results if u not in seen]
    todo = pending[:limit] if limit else pending
    log(f"[extract] {len(results)} result links, {len(results) - len(pending)} already "
        f"processed, {len(todo)} to visit now")

    totals = {"events": 0, "pages": 0, "errors": 0}
    with Fetcher(use_browser) as f:
        try:
            for i, url in enumerate(todo, 1):
                events, links, via, http, note = _process(f, url, today)
                if events is None:
                    save_page(db, url, None, "error", via, http, [], note, now_s())
                    totals["errors"] += 1
                    log(f"({i}/{len(todo)}) ✗ {note:<18} {url[:90]}")
                    continue

                if events and events[0]["method"] == "jsonld":
                    save_page(db, url, None, "events", via, http, events, note, now_s())
                    totals["events"] += len(events)
                    log(f"({i}/{len(todo)}) {len(events):>3} events [{via}] {url[:90]}")
                    continue

                # no structured events: follow event links, keep the page's own
                # heuristic guess only if the links gave nothing
                found = 0
                followed = [l for l in links if not db.execute(
                    "SELECT 1 FROM pages WHERE url=?", (l,)).fetchone()][:FOLLOW_LINKS]
                for link in followed:
                    ev2, _, via2, http2, note2 = _process(f, link, today)
                    status = ("error" if ev2 is None else
                              "events" if ev2 and ev2[0]["method"] == "jsonld" else
                              "heuristic" if ev2 else "none")
                    save_page(db, link, url, status, via2, http2, ev2 or [], note2, now_s())
                    totals["pages"] += 1
                    found += len(ev2 or [])

                own = events if not found else []
                status = "followed" if followed else ("heuristic" if own else "none")
                save_page(db, url, None, status, via, http, own,
                          f"{len(followed)} links followed" if followed else note, now_s())
                totals["events"] += found + len(own)
                tag = (f"{found:>3} events via {len(followed)} links" if followed else
                       f"{len(own):>3} events (heuristic)" if own else "  0 events")
                log(f"({i}/{len(todo)}) {tag} [{via}] {url[:80]}")
        except KeyboardInterrupt:
            log("stopped by you -- progress is saved, re-run to resume.")
            raise
    log(f"[extract] done: {totals['events']} events, {totals['pages']} extra pages "
        f"followed, {totals['errors']} errors")


# --------------------------------------------------------------------------
# enrich: listing pages often carry only name + date. Open each event's own
# page and fill in time, description, price, tickets, organizer.
# --------------------------------------------------------------------------

FILL = ("description", "end", "mode", "venue", "street", "locality", "region", "postal",
        "country", "address", "lat", "lng", "online_url", "organizer", "organizer_url",
        "price", "currency", "ticket_url", "image", "event_status")


def _norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())[:40]


def _needs_detail(d):
    return not d["description"] or "T" not in d["start"] or not d["price"]


def enrich(db, limit=None, use_browser=True):
    from .normalize import has_time

    known = {u for (u,) in db.execute("SELECT url FROM pages")}
    todo = []
    for eid, page_url, data in db.execute("SELECT id, page_url, data FROM events"):
        d = json.loads(data)
        url = d["event_url"]
        if (d["method"] == "jsonld" and url and url != page_url and url not in known
                and _needs_detail(d)):
            todo.append((eid, page_url, url, d))
            known.add(url)
    if limit:
        todo = todo[:limit]
    log(f"[enrich] {len(todo)} events missing time / description / price -> "
        "opening their own pages")

    filled = 0
    with Fetcher(use_browser) as f:
        try:
            for i, (eid, page_url, url, d) in enumerate(todo, 1):
                html, final, via, http, note = f.get(url)
                found = extract.parse(html, final, dt.date.today(), False)[0] if html else []
                match = next((e for e in found if _norm(e["name"]) == _norm(d["name"])),
                             found[0] if len(found) == 1 else None)
                changed = False
                if match:
                    if has_time(match["start"]) and not has_time(d["start"]):
                        d["start"], changed = match["start"], True
                    if has_time(match["end"]) and not has_time(d["end"]):
                        d["end"], changed = match["end"], True
                    for k in FILL:
                        if not str(d.get(k, "")).strip() and str(match.get(k, "")).strip():
                            d[k], changed = match[k], True
                if changed:
                    d["enriched_from"] = final
                    db.execute("UPDATE events SET data=? WHERE id=?", (dumps(d), eid))
                    filled += 1
                db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?,?)",
                           (url, page_url, "detail" if html else "error", via, http, 0,
                            "enriched" if changed else (note or "nothing new"), now_s()))
                db.commit()
                if i % 20 == 0 or i == len(todo):
                    log(f"    {i}/{len(todo)} visited, {filled} events enriched")
        except KeyboardInterrupt:
            log("stopped by you -- progress is saved, re-run to resume.")
            raise
