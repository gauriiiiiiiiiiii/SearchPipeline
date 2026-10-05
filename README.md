# India event pipeline

The pipeline searches the web for queries like `"upcoming events" coworking Pune`. It opens every
result and extracts the **events** on those pages: name, date, time, venue, address, city,
organizer, price, registration link and more. The output is one Excel workbook.

It costs nothing: there are no API keys, and it uses your installed Chrome when a site needs a real browser.

## Setup (one time)

```
python -m pip install -r requirements.txt
```

Google Chrome must be installed. You don't need `playwright install`.

## Run

```
run.bat                              # everything: search -> extract -> enrich -> export
run.bat search --cities "Pune,Mumbai"
run.bat extract                      # visit result links, pull events, then enrich
run.bat enrich                       # only open event pages to fill missing details
run.bat export                       # rebuild the Excel/CSV from what's stored
```

| Option | Meaning |
|---|---|
| `--cities "A,B"` | only these cities from `config.json` |
| `--pages N` | search result pages per query (10 links each) |
| `--limit N` | search: max queries; extract/enrich: max links this run |
| `--engine google` | Google instead of Bing (experimental, see below) |
| `--retry-errors` | extract: revisit links that failed last time |
| `--no-browser` | extract: plain HTTP only, never open Chrome |
| `--headless` | search: no window. A CAPTCHA then stops the run instead of waiting for you. |

**Stop anytime with Ctrl+C.** Every stage saves after each page, so re-running
resumes where it stopped and never re-fetches finished work.

## What it extracts

| Group | Columns |
|---|---|
| When | Status (Upcoming / Ongoing / Past / Date unknown), Start Date, Day, Start Time, End Date, End Time, Duration |
| Where | Event Format (Online / Offline / Hybrid), Venue, Area / Locality, City, State, Country, Full Address, Latitude, Longitude, Location Check |
| Who | Hosted By, Organizer Link |
| What | Event Type, Industry / Sector Focus, Keywords, Startup Relevant, Description, Image |
| Sign-up | Price, Registration Link, Event Link, Event Status (Scheduled / Cancelled / Postponed) |
| Provenance | Source Platform, Found On (the search result page), Search Queries, Search Cities, Extraction Method, Confidence |

## Output: `output\events.xlsx`

| Sheet | Contents |
|---|---|
| Summary | Counts of queries, links, pages crawled, events, drops and duplicates, plus a breakdown by city and platform |
| Upcoming events | Upcoming and ongoing events, soonest first |
| Past events | Past events, most recent first |
| All events | Everything, including events with no date |
| Search links | Every search result, with whether it was crawled and how many events it gave |
| Crawl log | Every page fetched: how it was fetched (HTTP or browser), its status and notes |

`events.csv` and `search_results.csv` hold the same data as plain CSV.
`pipeline.db` is the SQLite store used for resuming. Delete it to start over.

## How it works

```
 config.json (templates × cities)
        │
        ▼
 ┌─ STAGE 1 · SEARCH ─────────────────────────────────────────────────────┐
 │ real Chrome, Bing India market (cc=IN, mkt=en-IN), 15–35 s between pages│
 │ guards: network retry · CAPTCHA → you solve it · 403 → stop             │
 │         unrelated results (soft throttle) → pause + retry               │
 │ store result links (redirects unwrapped, tracking params removed)       │
 └─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─ STAGE 2 · EXTRACT (per result link) ──────────────────────────────────┐
 │ fetch: plain HTTP ──blocked / JS-only?──► real Chrome                   │
 │   │                                                                     │
 │   ├─ schema.org JSON-LD Event data on the page? ──► save events         │
 │   │                                                                     │
 │   └─ no ──► find event links on the page                                │
 │              (lu.ma, Meetup, Eventbrite, Townscript, Unstop,            │
 │               Startup Grind, same-site /events/<slug> …)                │
 │              open up to 15 of them, extract each one:                   │
 │                JSON-LD, else heuristic (title + date + venue)           │
 │              nothing found? heuristic on the page itself                │
 ├─ STAGE 2b · ENRICH ─────────────────────────────────────────────────────┤
 │ events with no time / description / price ──► open the event's own     │
 │ page, read its JSON-LD, fill in the blanks                               │
 └─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─ STAGE 3 · EXPORT ─────────────────────────────────────────────────────┐
 │ normalize: dates → IST, Upcoming/Past, Online/Offline, city, India check│
 │ classify: event type · sector · keyword tags · startup relevant         │
 │ drop: noise titles (real estate, astrology …), events outside India     │
 │ merge duplicates (same link, or same name + date) across platforms      │
 │ write events.xlsx · events.csv · search_results.csv                     │
 └─────────────────────────────────────────────────────────────────────────┘
```

## Code layout

```
run.py                  command line: stages and options
config.json             queries, cities, extract settings
pipeline/
  settings.py           paths + config loading
  db.py                 SQLite schema (results, done, pages, events)
  browser.py            launches real Chrome with a persistent profile
  search.py             stage 1: Bing / Google engines
  fetch.py              HTTP-then-Chrome page fetcher, polite per-site delays
  extract.py            JSON-LD parser, event-link finder, heuristic extractor
  crawl.py              stage 2 + 2b: drives fetch + extract, follows links, enriches
  normalize.py          dates, status, city / India, classification, dedupe
  export.py             stage 3: Excel + CSV
```

## Settings in `config.json`

- `templates`: the search phrases. `{city}` is replaced by each city name.
- `cities`: the names used in queries. The value is Google's location name, which only `--engine google` uses.
- `pages`: search result pages per query.
- `extract.follow_links_per_page`: how many event links to open from a page that has no structured data.
- `extract.keep_outside_india`: `true` keeps events whose address is in another country.
- `extract.description_chars`: the length descriptions are trimmed to.
- `extract.seconds_between_hits_same_site`: the politeness delay between requests to the same site.

## Engines: what to expect (tested Oct 2026)

| Engine | Status |
|---|---|
| `bing` | Works. It returns real URLs and India-relevant results. It occasionally serves generic results that ignore the query. The script detects this and retries. |
| `google` | Hard to use. Result links are hidden behind encrypted `/goto` redirects, and Google returned a 403 block after about 5 queries. When that happens the script stops cleanly. |

## Known limits

- **Sites without structured data** fall back to a heuristic, marked `Confidence = low`.
  Check those rows by hand. Examples are small incubator and coworking websites.
- **Listing aggregators** such as AllEvents and StayHappening return many general business
  events, not only startup ones. Filter on the `Startup Relevant` column.
- **Some sites block every bot.** These end up as `error` in the Crawl log and are retried with `--retry-errors`.
- **Throughput:** one search page takes about 25 s, so a full 14-city search takes about 1 hour. Extraction
  takes about 2 s per page per site.
