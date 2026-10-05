"""
India event pipeline:  search -> extract -> export.

    python run.py                    # all three stages
    python run.py search             # stage 1: search engine -> result links
    python run.py extract            # stage 2: visit links -> events (+ enrich)
    python run.py enrich             # stage 2b only: open each event's own page
                                     #   to fill time / description / price
    python run.py export             # stage 3: output/events.xlsx + csv

Options (any stage that uses them):
    --engine bing|google   search engine (default bing; google is experimental)
    --cities "Pune,Mumbai" subset of the cities in config.json
    --pages N              result pages per query (default from config.json)
    --limit N              search: max queries / extract: max links this run
    --retry-errors         extract: revisit links that failed last time
    --no-browser           extract: plain HTTP only, never open Chrome
    --headless             search: no window (CAPTCHA then stops the run)

Every stage saves as it goes; stop with Ctrl+C and re-run to resume.
"""

import argparse
import sys

from pipeline import crawl, db as store, export, search
from pipeline.settings import PAGES


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", nargs="?", default="all",
                    choices=["all", "search", "extract", "enrich", "export"])
    ap.add_argument("--engine", choices=list(search.ENGINES), default="bing")
    ap.add_argument("--cities")
    ap.add_argument("--pages", type=int, default=PAGES)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()

    db = store.connect()
    try:
        if a.stage in ("all", "search"):
            search.run(db, a.engine, a.cities, a.pages, a.limit, a.headless)
        lim = None if a.stage == "all" else a.limit
        if a.stage in ("all", "extract"):
            crawl.run(db, lim, a.retry_errors, not a.no_browser)
        if a.stage in ("all", "extract", "enrich"):
            crawl.enrich(db, lim, not a.no_browser)
    except KeyboardInterrupt:
        pass
    finally:
        export.run(db)


if __name__ == "__main__":
    sys.exit(main())
