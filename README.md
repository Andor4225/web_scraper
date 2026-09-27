# Async Web Crawler

A concurrent, polite, domain-constrained web crawler built with Python, `asyncio`, `aiohttp` and
`BeautifulSoup4`. Starting from one URL, it crawls the pages of that site, extracts page metadata
(heading, first paragraph, internal/external links, images) and exports it to a JSON or CSV report.

## Features

- **Bounded concurrency**: a URL queue served by a fixed pool of worker tasks, so memory and
  open connections stay bounded no matter how many links a page has.
- **Exact page limit**: `--max-pages` counts pages crawled successfully. Failed pages don't use
  up the budget.
- **Stays on one site**: links are followed only on the start URL's host (the `www.` prefix is
  ignored unless `--strict-host` is set). If the start URL redirects to another host (e.g.
  `example.com` → `www.example.com`), that host becomes the site. Other redirects are followed
  one hop at a time, and off-site redirects are skipped rather than fetched.
- **Polite by default**: obeys `robots.txt` (including `Crawl-delay`, capped at 30s), sends an identifying
  `User-Agent`, and supports a fixed delay between requests.
- **Resilient**: per-request timeouts, retries with exponential backoff for `429`/`5xx`/network
  errors (honouring `Retry-After`), a response size cap, and charset-safe decoding.
- **Accurate URL handling**: case-insensitive hosts, query strings kept (`?page=2` is its own
  page), fragments dropped, `<base href>` honoured, and duplicate and `mailto:`/`javascript:`
  links ignored.
- **Graceful Ctrl+C**: the first press stops the crawl and still writes a partial report; a
  second press aborts.
- **JSON or CSV reports** with a summary, failed URLs (with status codes) and skipped URLs.
  Reports are written atomically.

## Installation

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

## Usage

```bash
# Crawl up to 10 pages with 3 concurrent requests (defaults) and write report.json
uv run web-scraper https://example.com

# 5 concurrent requests, 50 pages, at most 2 links deep, 0.5s between requests, CSV output
uv run web-scraper example.com -c 5 -n 50 -d 2 --delay 0.5 -o site.csv
```

`https://` is assumed when the scheme is omitted. The original positional form still works:
`uv run main.py https://example.com 5 50` (URL, concurrency, max pages).

| Option | Default | Description |
| --- | --- | --- |
| `-c`, `--concurrency` | `3` | Number of parallel requests |
| `-n`, `--max-pages` | `10` | Number of pages to crawl successfully |
| `-d`, `--max-depth` | unlimited | Maximum link depth from the start URL (start URL = 0) |
| `--delay` | `0` | Seconds between requests (a larger robots.txt `Crawl-delay` wins) |
| `--timeout` | `15` | Per-request timeout in seconds |
| `--retries` | `2` | Retries for 429/5xx responses and network errors |
| `--user-agent` | `web-scraper/0.2 (...)` | `User-Agent` header |
| `--ignore-robots` | off | Don't fetch or obey `robots.txt` |
| `--strict-host` | off | Treat `www.example.com` and `example.com` as different sites |
| `-o`, `--output` | `report.json` | Report path (`.csv` extension selects CSV) |
| `-f`, `--format` | from extension | `json` or `csv` |
| `-v` / `-q` | | Verbose (debug) / quiet (errors only) logging |

Exit codes: `0` success, `1` no pages could be crawled, `2` invalid arguments, `130` interrupted.

`HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` environment variables are honoured.

## Report format

JSON (`report.json`):

```json
{
  "summary": {
    "base_url": "https://example.com",
    "generated_at": "2026-09-26T12:00:00+00:00",
    "pages_crawled": 2,
    "pages_failed": 1,
    "pages_skipped": 1,
    "elapsed_seconds": 1.42,
    "interrupted": false
  },
  "pages": [
    {
      "url": "https://example.com",
      "heading": "Example Domain",
      "first_paragraph": "This domain is for use in examples.",
      "outgoing_links": ["https://example.com/about", "https://www.iana.org/"],
      "internal_links": ["https://example.com/about"],
      "external_links": ["https://www.iana.org/"],
      "image_urls": ["https://example.com/logo.png"],
      "status_code": 200,
      "depth": 0
    }
  ],
  "failures": [
    { "url": "https://example.com/old", "status_code": 404, "error": "HTTP error: status code 404" }
  ],
  "skipped": [
    { "url": "https://example.com/admin", "reason": "disallowed by robots.txt: https://example.com/admin" }
  ]
}
```

CSV has one row per page (link lists are space-separated, with link counts), followed by one
row per failed URL (with its `error`) and one per skipped URL (`error` is `skipped: <reason>`).

> **Upgrading from 0.1:** `report.json` used to be a bare list of pages. It's now an object, and
> the page list is under `"pages"`. See the [CHANGELOG](CHANGELOG.md) for how to update scripts.

## Using it as a library

```python
import asyncio
from web_scraper import CrawlConfig, crawl_site, write_report

result = asyncio.run(crawl_site("https://example.com", CrawlConfig(max_pages=25, delay=0.5)))
print(len(result.pages), "pages,", len(result.failures), "failures")
write_report(result, "report.json")
```

## Development

```bash
uv sync                      # installs runtime and dev dependencies
uv run pytest                # tests (a local aiohttp server; no network needed)
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # strict type checking
```

CI (GitHub Actions) runs lint, format check, mypy and the tests on Python 3.12 and 3.13.

### Project layout

```
web_scraper/
  cli.py       command-line interface
  crawler.py   AsyncCrawler: queue/workers, fetching, retries, redirects
  parser.py    HTML extraction (one parse per page)
  robots.py    robots.txt cache
  report.py    JSON/CSV report writer
  urls.py      URL normalization and same-site checks
tests/         pytest suite
main.py        backwards-compatible entry point
```
