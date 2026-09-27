# Changelog

## 0.2.0

### ⚠️ Breaking change: new `report.json` layout

One thing to know when you next use it: `report.json` now has a different layout. The list of
pages is under `"pages"`, next to a `"summary"`, `"failures"` and `"skipped"`. If you have any
scripts that read the old report, they'll need a small update.

**Before (0.1):** the file was a bare list of pages.

```json
[
  { "url": "https://example.com", "heading": "...", "first_paragraph": "...", "outgoing_links": [], "image_urls": [] }
]
```

**After (0.2):** the file is an object.

```json
{
  "summary":  { "base_url": "...", "pages_crawled": 1, "pages_failed": 0, "pages_skipped": 0, "...": "..." },
  "pages":    [ { "url": "https://example.com", "heading": "...", "...": "..." } ],
  "failures": [ { "url": "...", "status_code": 404, "error": "..." } ],
  "skipped":  [ { "url": "...", "reason": "..." } ]
}
```

**Updating a script** usually means one change, reading `"pages"` instead of the top level:

```python
import json

with open("report.json", encoding="utf-8") as f:
    report = json.load(f)

pages = report["pages"]  # was: pages = report
```

Every page keeps the fields it had in 0.1 (`url`, `heading`, `first_paragraph`,
`outgoing_links`, `image_urls`) and gains `internal_links`, `external_links`, `status_code`
and `depth`.

### Other changes

- The code is now a `web_scraper` package with a `web-scraper` command. `uv run main.py URL
  [concurrency] [max_pages]` still works.
- The crawler now respects `robots.txt`, retries temporary errors, stays on one site (including
  across redirects) and counts `--max-pages` as successfully crawled pages only.
- Reports can be written as CSV (`-o report.csv`), and Ctrl+C now saves a partial report.
- The Python modules `crawl.py` and `json_report.py` were removed. Import from `web_scraper`
  instead.

See the [README](README.md) for all options.
