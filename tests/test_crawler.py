"""Integration tests for AsyncCrawler against a fake site served by aiohttp_server."""

import asyncio
import contextlib
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from aiohttp import web

from web_scraper.crawler import AsyncCrawler, CrawlConfig, CrawlResult, crawl_site
from web_scraper.urls import normalize_url

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
Route = str | Handler


def html(*links: str, title: str = "Page") -> str:
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return f"<html><body><h1>{title}</h1><p>Body of {title}.</p>{anchors}</body></html>"


def fast_config(**overrides: Any) -> CrawlConfig:
    options: dict[str, Any] = {"retry_backoff": 0, "timeout": 5.0, "max_pages": 50}
    options.update(overrides)
    return CrawlConfig(**options)


class Site:
    """A fake site: maps request path -> HTML string or aiohttp handler.

    Unknown paths (including /robots.txt when not given) return 404. Every request is
    counted in ``hits`` (keyed by path plus query string) and concurrent in-flight
    requests (excluding /robots.txt) are tracked in ``max_in_flight``.
    """

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.hits: Counter[str] = Counter()
        self.in_flight = 0
        self.max_in_flight = 0
        self.app = web.Application()
        self.app.router.add_route("GET", "/{tail:.*}", self._dispatch)

    async def _dispatch(self, request: web.Request) -> web.StreamResponse:
        self.hits[request.path_qs] += 1
        counted = request.path != "/robots.txt"
        if counted:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            route = self.routes.get(request.path)
            if route is None:
                return web.Response(status=404, text="not found")
            if isinstance(route, str):
                if request.path == "/robots.txt":
                    return web.Response(text=route, content_type="text/plain")
                return web.Response(text=route, content_type="text/html")
            return await route(request)
        finally:
            if counted:
                self.in_flight -= 1


ServeFn = Callable[[dict[str, Route]], Awaitable[tuple[Site, str]]]


@pytest.fixture
def serve(aiohttp_server: Any) -> ServeFn:
    async def _serve(routes: dict[str, Route]) -> tuple[Site, str]:
        site = Site(routes)
        server = await aiohttp_server(site.app, host="127.0.0.1")
        return site, str(server.make_url("/"))

    return _serve


async def crawl(base: str, **overrides: Any) -> CrawlResult:
    async with AsyncCrawler(base, fast_config(**overrides)) as crawler:
        return await crawler.crawl()


def key(base: str, path: str) -> str:
    return normalize_url(base.rstrip("/") + path)


# 1. basic crawl


async def test_crawls_all_reachable_pages(serve: ServeFn) -> None:
    site, base = await serve(
        {
            "/": html("/a", "/b", title="Home"),
            "/a": html("/", "/b", title="A"),
            "/b": html(title="B"),
            "/orphan": html(title="Orphan"),
        }
    )
    result = await crawl(base)

    assert set(result.pages) == {key(base, "/"), key(base, "/a"), key(base, "/b")}
    root = result.pages[key(base, "/")]
    assert root["status_code"] == 200
    assert root["depth"] == 0
    assert root["heading"] == "Home"
    assert result.pages[key(base, "/a")]["depth"] == 1
    assert result.pages[key(base, "/b")]["depth"] == 1
    assert result.failures == []
    assert result.skipped == []
    assert not result.interrupted
    assert site.hits["/orphan"] == 0


async def test_crawl_site_helper(serve: ServeFn) -> None:
    _, base = await serve({"/": html("/a"), "/a": html()})
    result = await crawl_site(base, fast_config())
    assert set(result.pages) == {key(base, "/"), key(base, "/a")}
    assert result.elapsed > 0


# 2. external links


async def test_external_links_listed_not_fetched(serve: ServeFn) -> None:
    _, base = await serve({"/": html("https://example.org/", "/a"), "/a": html()})
    result = await crawl(base)

    root = result.pages[key(base, "/")]
    assert root["external_links"] == ["https://example.org/"]
    assert normalize_url("https://example.org/") not in result.pages
    assert set(result.pages) == {key(base, "/"), key(base, "/a")}


# 3. query strings


async def test_query_strings_are_distinct_pages(serve: ServeFn) -> None:
    site, base = await serve(
        {"/": html("/list?page=1", "/list?page=2"), "/list": html("/list?page=1")}
    )
    result = await crawl(base)

    assert key(base, "/list?page=1") in result.pages
    assert key(base, "/list?page=2") in result.pages
    assert site.hits["/list?page=1"] == 1
    assert site.hits["/list?page=2"] == 1


# 4. duplicates / fragments / trailing slash


async def test_duplicate_and_fragment_links_fetched_once(serve: ServeFn) -> None:
    site, base = await serve({"/": html("/a", "/a#top", "/a/", "/a"), "/a": html("/a#x", "/")})
    result = await crawl(base)

    assert site.hits["/a"] == 1
    assert site.hits["/a/"] == 0
    assert site.hits["/"] == 1
    assert set(result.pages) == {key(base, "/"), key(base, "/a")}


# 5. max_pages exactness with failures


async def test_max_pages_is_exact_and_failures_do_not_count(serve: ServeFn) -> None:
    ok = [f"/ok{i}" for i in range(1, 6)]
    routes: dict[str, Route] = {"/": html("/missing1", "/missing2", *ok)}
    routes.update({path: html() for path in ok})
    _, base = await serve(routes)

    result = await crawl(base, max_pages=4, max_concurrency=3)

    assert len(result.pages) == 4
    failed = {f["url"]: f for f in result.failures}
    for path in ("/missing1", "/missing2"):
        failure = failed[base.rstrip("/") + path]
        assert failure["status_code"] == 404
    assert not result.interrupted


# 6. max_depth


async def test_max_depth_limits_traversal(serve: ServeFn) -> None:
    site, base = await serve({"/": html("/1"), "/1": html("/2"), "/2": html("/3"), "/3": html()})
    result = await crawl(base, max_depth=1)

    assert set(result.pages) == {key(base, "/"), key(base, "/1")}
    assert site.hits["/2"] == 0


# 7. same-site redirects


def redirect_to(location: str, status: int = 301) -> Handler:
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(status=status, headers={"Location": location})

    return handler


async def test_same_site_redirect_recorded_under_final_url(serve: ServeFn) -> None:
    site, base = await serve(
        {
            "/": html("/old"),
            "/old": redirect_to("/dir/new"),
            "/dir/new": html("sibling", title="New"),
            "/dir/sibling": html(title="Sibling"),
        }
    )
    result = await crawl(base)

    assert key(base, "/old") not in result.pages
    new = result.pages[key(base, "/dir/new")]
    assert new["url"] == base.rstrip("/") + "/dir/new"
    assert new["heading"] == "New"
    assert new["depth"] == 1
    # Relative link resolved against the redirect target, not /old.
    assert key(base, "/dir/sibling") in result.pages
    assert site.hits["/sibling"] == 0


async def test_redirect_target_linked_directly_recorded_once(serve: ServeFn) -> None:
    _, base = await serve(
        {"/": html("/old", "/new"), "/old": redirect_to("/new"), "/new": html(title="New")}
    )
    result = await crawl(base)

    assert set(result.pages) == {key(base, "/"), key(base, "/new")}
    assert result.failures == []


async def test_redirect_target_linked_directly_fetched_once(serve: ServeFn) -> None:
    site, base = await serve(
        {"/": html("/old", "/new"), "/old": redirect_to("/new"), "/new": html(title="New")}
    )
    await crawl(base, max_concurrency=1)

    assert site.hits["/new"] == 1


async def test_redirect_target_discovered_later_fetched_once(serve: ServeFn) -> None:
    # /new is first reached via the redirect, then linked from /other.
    site, base = await serve(
        {
            "/": html("/old", "/other"),
            "/old": redirect_to("/new"),
            "/new": html(title="New"),
            "/other": html("/new"),
        }
    )
    result = await crawl(base, max_concurrency=1)

    assert key(base, "/new") in result.pages
    assert site.hits["/new"] == 1


async def test_trailing_slash_redirect_is_followed(serve: ServeFn) -> None:
    # /folder and /folder/ normalize to the same key; the redirect must still be followed.
    _, base = await serve(
        {"/": html("/folder"), "/folder": redirect_to("/folder/"), "/folder/": html(title="F")}
    )
    result = await crawl(base)

    assert result.pages[key(base, "/folder")]["heading"] == "F"
    assert result.failures == []
    assert result.skipped == []


# 8. off-site redirect


async def test_off_site_redirect_is_skipped(serve: ServeFn) -> None:
    _, base = await serve({"/": html("/out"), "/out": redirect_to("https://example.org/x", 302)})
    result = await crawl(base)

    skipped = {s["url"]: s["reason"] for s in result.skipped}
    out_url = base.rstrip("/") + "/out"
    assert out_url in skipped
    assert "example.org" in skipped[out_url]
    assert normalize_url("https://example.org/x") not in result.pages
    assert key(base, "/out") not in result.pages
    assert result.failures == []


# 9. robots.txt


ROBOTS_SITE: dict[str, Route] = {
    "/robots.txt": "User-agent: *\nDisallow: /private\n",
    "/": html("/private", "/public"),
    "/private": html(),
    "/public": html(),
}


async def test_robots_disallow_respected(serve: ServeFn) -> None:
    site, base = await serve(dict(ROBOTS_SITE))
    result = await crawl(base)

    assert site.hits["/private"] == 0
    assert [s["url"] for s in result.skipped] == [base.rstrip("/") + "/private"]
    assert set(result.pages) == {key(base, "/"), key(base, "/public")}
    assert site.hits["/robots.txt"] == 1


async def test_robots_ignored_when_disabled(serve: ServeFn) -> None:
    site, base = await serve(dict(ROBOTS_SITE))
    result = await crawl(base, respect_robots=False)

    assert site.hits["/private"] == 1
    assert site.hits["/robots.txt"] == 0
    assert key(base, "/private") in result.pages
    assert result.skipped == []


async def test_robots_404_allows_everything(serve: ServeFn) -> None:
    site, base = await serve({"/": html("/a"), "/a": html()})
    result = await crawl(base)

    assert site.hits["/robots.txt"] == 1
    assert set(result.pages) == {key(base, "/"), key(base, "/a")}


async def test_robots_500_disallows_everything(serve: ServeFn) -> None:
    async def broken(request: web.Request) -> web.StreamResponse:
        return web.Response(status=500)

    site, base = await serve({"/robots.txt": broken, "/": html("/a"), "/a": html()})
    result = await crawl(base)

    assert result.pages == {}
    assert [s["url"] for s in result.skipped] == [base]
    assert site.hits["/"] == 0


async def test_robots_crawl_delay_honoured(serve: ServeFn) -> None:
    # urllib.robotparser only accepts integer Crawl-delay values.
    _, base = await serve(
        {"/robots.txt": "User-agent: *\nCrawl-delay: 1\n", "/": html("/a"), "/a": html()}
    )
    started = time.monotonic()
    result = await crawl(base)

    assert len(result.pages) == 2
    # The second page request must wait one crawl-delay after the first.
    assert time.monotonic() - started >= 0.95


# 10. retries


def flaky(failures: int, status: int = 503, headers: dict[str, str] | None = None) -> Handler:
    calls = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal calls
        calls += 1
        if calls <= failures:
            return web.Response(status=status, headers=headers)
        return web.Response(text=html(title="Recovered"), content_type="text/html")

    return handler


async def test_retry_503_then_success(serve: ServeFn) -> None:
    site, base = await serve({"/": html("/flaky"), "/flaky": flaky(2)})
    result = await crawl(base, max_retries=2)

    assert site.hits["/flaky"] == 3
    assert result.pages[key(base, "/flaky")]["heading"] == "Recovered"
    assert result.failures == []


async def test_no_retries_records_503_failure(serve: ServeFn) -> None:
    site, base = await serve({"/": html("/flaky"), "/flaky": flaky(2)})
    result = await crawl(base, max_retries=0)

    assert site.hits["/flaky"] == 1
    assert key(base, "/flaky") not in result.pages
    assert result.failures == [
        {
            "url": base.rstrip("/") + "/flaky",
            "status_code": 503,
            "error": "HTTP error: status code 503",
        }
    ]


async def test_retry_429_with_retry_after(serve: ServeFn) -> None:
    site, base = await serve(
        {"/": html("/limited"), "/limited": flaky(1, 429, {"Retry-After": "0"})}
    )
    result = await crawl(base, max_retries=1)

    assert site.hits["/limited"] == 2
    assert key(base, "/limited") in result.pages


# 11. content types


def serve_bytes(body: bytes, content_type: str) -> Handler:
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(body=body, headers={"Content-Type": content_type})

    return handler


@pytest.mark.parametrize("content_type", ["application/json", "image/png"])
async def test_non_html_content_type_is_failure(serve: ServeFn, content_type: str) -> None:
    _, base = await serve({"/": html("/data"), "/data": serve_bytes(b"{}", content_type)})
    result = await crawl(base)

    assert key(base, "/data") not in result.pages
    [failure] = result.failures
    assert failure["url"] == base.rstrip("/") + "/data"
    assert failure["status_code"] == 200
    assert "content type" in failure["error"]


async def test_xhtml_content_type_is_accepted(serve: ServeFn) -> None:
    body = html(title="XHTML").encode()
    _, base = await serve(
        {"/": html("/x"), "/x": serve_bytes(body, "application/xhtml+xml; charset=utf-8")}
    )
    result = await crawl(base)

    assert result.pages[key(base, "/x")]["heading"] == "XHTML"
    assert result.failures == []


# 12. response size limit


async def test_response_too_large_is_failure(serve: ServeFn) -> None:
    big = html(title="Big") + "<!--" + "x" * 5000 + "-->"
    _, base = await serve({"/": html("/big"), "/big": big})
    result = await crawl(base, max_response_bytes=2000)

    assert key(base, "/big") not in result.pages
    [failure] = result.failures
    assert "exceeds 2000 bytes" in failure["error"]
    assert key(base, "/") in result.pages


async def test_chunked_response_too_large_is_failure(serve: ServeFn) -> None:
    async def streamed(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/html"})
        response.enable_chunked_encoding()
        await response.prepare(request)
        for _ in range(10):
            await response.write(b"<p>" + b"x" * 1000 + b"</p>")
        await response.write_eof()
        return response

    _, base = await serve({"/": html("/stream"), "/stream": streamed})
    result = await crawl(base, max_response_bytes=2000)

    assert key(base, "/stream") not in result.pages
    [failure] = result.failures
    assert "exceeds 2000 bytes" in failure["error"]


# 13. timeout


async def test_timeout_recorded_as_failure(serve: ServeFn) -> None:
    release = asyncio.Event()

    async def slow(request: web.Request) -> web.StreamResponse:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), timeout=2)
        return web.Response(text=html(), content_type="text/html")

    _, base = await serve({"/": html("/slow", "/fast"), "/slow": slow, "/fast": html()})
    started = time.monotonic()
    try:
        result = await crawl(base, timeout=0.2, max_retries=0)
    finally:
        release.set()

    assert time.monotonic() - started < 1.5
    assert set(result.pages) == {key(base, "/"), key(base, "/fast")}
    [failure] = result.failures
    assert failure["url"] == base.rstrip("/") + "/slow"
    assert failure["status_code"] is None
    assert "Timeout" in failure["error"]


# 14. bad charset


async def test_bogus_charset_does_not_crash(serve: ServeFn) -> None:
    body = "<html><body><h1>Café</h1><p>naïve</p></body></html>".encode("latin-1")
    _, base = await serve(
        {"/": html("/latin"), "/latin": serve_bytes(body, "text/html; charset=bogus-charset")}
    )
    result = await crawl(base)

    page = result.pages[key(base, "/latin")]
    assert page["status_code"] == 200
    assert page["heading"].startswith("Caf")
    assert result.failures == []


# 15. request_stop


async def test_request_stop_interrupts_crawl(serve: ServeFn) -> None:
    release = asyncio.Event()
    holder: dict[str, AsyncCrawler] = {}

    async def slow(request: web.Request) -> web.StreamResponse:
        holder["crawler"].request_stop()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), timeout=3)
        return web.Response(text=html(), content_type="text/html")

    slow_paths = [f"/slow{i}" for i in range(5)]
    routes: dict[str, Route] = {"/": html(*slow_paths)}
    routes.update({path: slow for path in slow_paths})
    _, base = await serve(routes)

    started = time.monotonic()
    try:
        async with AsyncCrawler(base, fast_config(max_concurrency=2)) as crawler:
            holder["crawler"] = crawler
            result = await crawler.crawl()
    finally:
        release.set()

    assert time.monotonic() - started < 1.5
    assert result.interrupted is True
    assert set(result.pages) == {key(base, "/")}


async def test_request_stop_via_call_later(serve: ServeFn) -> None:
    release = asyncio.Event()

    async def slow(request: web.Request) -> web.StreamResponse:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), timeout=3)
        return web.Response(text=html(), content_type="text/html")

    _, base = await serve({"/": html("/slow"), "/slow": slow})
    started = time.monotonic()
    try:
        async with AsyncCrawler(base, fast_config()) as crawler:
            asyncio.get_running_loop().call_later(0.3, crawler.request_stop)
            result = await crawler.crawl()
    finally:
        release.set()

    assert 0.25 <= time.monotonic() - started < 1.5
    assert result.interrupted is True
    assert key(base, "/") in result.pages


# 16. concurrency bound


async def test_max_concurrency_bounds_in_flight_requests(serve: ServeFn) -> None:
    async def sleepy(request: web.Request) -> web.StreamResponse:
        await asyncio.sleep(0.05)
        return web.Response(text=html(), content_type="text/html")

    paths = [f"/p{i}" for i in range(8)]
    routes: dict[str, Route] = {"/": html(*paths)}
    routes.update({path: sleepy for path in paths})
    site, base = await serve(routes)

    result = await crawl(base, max_concurrency=2)

    assert len(result.pages) == 9
    assert site.max_in_flight == 2


# 17. www equivalence (unit level: 127.0.0.1 has no www variant)


@pytest.mark.parametrize(("ignore_www", "expected"), [(True, 1), (False, 0)])
async def test_enqueue_www_equivalence(ignore_www: bool, expected: int) -> None:
    crawler = AsyncCrawler("https://example.com/", CrawlConfig(ignore_www=ignore_www))
    crawler._enqueue("https://www.example.com/page", 1)
    crawler._enqueue("https://other.com/page", 1)
    assert crawler._queue.qsize() == expected


# 18. validation


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_pages": 0},
        {"max_concurrency": 0},
        {"timeout": 0},
        {"max_depth": -1},
        {"max_retries": -1},
        {"retry_backoff": -1},
        {"max_response_bytes": 0},
    ],
)
def test_crawl_config_validation(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CrawlConfig(**overrides)


@pytest.mark.parametrize("url", ["ftp://example.com/", "example.com", "mailto:a@b.c", "http://"])
def test_crawler_rejects_non_http_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        AsyncCrawler(url)
