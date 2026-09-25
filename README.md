# Async Web Crawler

A concurrent, domain-constrained web crawler built with Python, `aiohttp`, `asyncio`, and `BeautifulSoup4`. It crawls pages asynchronously, extracts structured metadata (headings, initial paragraph, internal/external links, images), and exports deduplicated results to a formatted JSON report.

## Features

- **Asynchronous Network I/O**: High-throughput fetching using `aiohttp.ClientSession`.
- **Concurrency Rate Limiting**: Controlled execution via `asyncio.Semaphore`.
- **Domain Constraint**: Automatically keeps traversal strictly within the base URL's origin domain.
- **Race Condition Safety**: Uses `asyncio.Lock` for deduplicating visited URLs and aggregating page state.
- **Configurable Crawl Limits**: Stops cleanly when `max_pages` is reached while letting in-flight requests resolve.
- **JSON Report Generator**: Exports sorted, structured document models to `report.json`.

## Installation & Setup

Ensure you have [uv](https://github.com/astral-sh/uv) installed:

# Install dependencies and sync virtualenv
uv sync
