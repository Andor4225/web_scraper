"""Concurrent, same-site web crawler built on a URL queue and a fixed pool of workers."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self, TypedDict
from urllib.parse import urljoin

import aiohttp
from bs4 import UnicodeDammit

from .parser import ParsedPage, extract_page_data
from .robots import RobotsCache
from .urls import is_http_url, is_same_site, normalize_url, strip_fragment

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "web-scraper/0.2 (+https://github.com/Andor4225/web_scraper)"
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_REDIRECTS = 10
MAX_RETRY_AFTER = 60.0
MAX_CRAWL_DELAY = 30.0
ROBOTS_MAX_BYTES = 512 * 1024
_CHUNK_SIZE = 64 * 1024


class CrawlError(Exception):
    """A page could not be crawled; recorded as a failure in the report."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HTTPStatusError(CrawlError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP error: status code {status_code}", status_code)


class NotHTMLError(CrawlError):
    def __init__(self, content_type: str, status_code: int) -> None:
        super().__init__(f"unsupported content type: {content_type}", status_code)


class ResponseTooLargeError(CrawlError):
    def __init__(self, limit: int, status_code: int) -> None:
        super().__init__(f"response exceeds {limit} bytes", status_code)


class RedirectError(CrawlError):
    pass


class SkipURL(Exception):
    """A page was deliberately not crawled (robots.txt, off-site redirect)."""


class _AlreadySeen(Exception):
    """A redirect leads to a URL that is already queued or crawled."""


@dataclass(frozen=True, kw_only=True)
class CrawlConfig:
    max_concurrency: int = 3
    max_pages: int = 10
    max_depth: int | None = None
    timeout: float = 15.0
    max_retries: int = 2
    retry_backoff: float = 0.5
    delay: float = 0.0
    user_agent: str = DEFAULT_USER_AGENT
    respect_robots: bool = True
    max_response_bytes: int = 5 * 1024 * 1024
    ignore_www: bool = True

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if self.max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if self.max_depth is not None and self.max_depth < 0:
            raise ValueError("max_depth must not be negative")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.retry_backoff < 0 or self.delay < 0:
            raise ValueError("retry_backoff and delay must not be negative")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")


class PageData(ParsedPage):
    status_code: int
    depth: int


class Failure(TypedDict):
    url: str
    status_code: int | None
    error: str


class Skipped(TypedDict):
    url: str
    reason: str


@dataclass
class CrawlResult:
    base_url: str
    pages: dict[str, PageData] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    elapsed: float = 0.0
    interrupted: bool = False


@dataclass
class _Response:
    url: str
    status: int
    location: str | None = None
    html: str | None = None


def _describe(exc: BaseException) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def decode_html(body: bytes, charset: str | None) -> str:
    """Decode a response body without ever raising on bad or missing charsets."""
    if charset:
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            pass
    markup = UnicodeDammit(body, is_html=True).unicode_markup
    return markup if markup is not None else body.decode("utf-8", errors="replace")


async def _read_limited(
    response: aiohttp.ClientResponse, limit: int, *, truncate: bool = False
) -> bytes:
    """Read at most ``limit`` bytes; raise if the body is larger, unless ``truncate``."""
    if not truncate and response.content_length is not None and response.content_length > limit:
        raise ResponseTooLargeError(limit, response.status)
    body = bytearray()
    async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
        body.extend(chunk)
        if len(body) > limit:
            if truncate:
                return bytes(body[:limit])
            raise ResponseTooLargeError(limit, response.status)
    return bytes(body)


class AsyncCrawler:
    """Crawl every page of one site, up to ``config.max_pages`` successful pages.

    Use as an async context manager, or pass an existing ``session``.
    """

    def __init__(
        self,
        base_url: str,
        config: CrawlConfig | None = None,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not is_http_url(base_url):
            raise ValueError(f"not an http(s) URL: {base_url!r}")
        self.base_url = base_url
        # The site being crawled. Starts as the base URL and moves to wherever the
        # start page finally lands if it redirects to another host (e.g. to www.).
        self._site_url = base_url
        self.config = config or CrawlConfig()
        self._session = session
        self._owns_session = session is None

        self.result = CrawlResult(base_url=base_url)
        self._seen: set[str] = set()
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._slots = asyncio.Condition()
        self._in_flight = 0
        self._stop = asyncio.Event()
        self._running = False

        self._throttle_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._delay = self.config.delay
        self._robots = (
            RobotsCache(self._fetch_text, self.config.user_agent)
            if self.config.respect_robots
            else None
        )

    async def __aenter__(self) -> Self:
        if self._session is None:
            # trust_env: honour HTTP(S)_PROXY and NO_PROXY like curl and requests do.
            self._session = aiohttp.ClientSession(trust_env=True)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("ClientSession is not initialized. Use inside 'async with'.")
        return self._session

    def request_stop(self) -> None:
        """Stop gracefully (e.g. on Ctrl+C); pages crawled so far are kept.

        Has no effect once the crawl has finished.
        """
        if self._running and not self._stop.is_set():
            self.result.interrupted = True
            self._stop.set()

    async def crawl(self) -> CrawlResult:
        started = time.monotonic()
        self._running = True
        try:
            await self._configure_crawl_delay()
            self._enqueue(self.base_url, 0)
            workers = [
                asyncio.create_task(self._worker(), name=f"crawl-worker-{i}")
                for i in range(self.config.max_concurrency)
            ]
            drained = asyncio.create_task(self._queue.join())
            stopped = asyncio.create_task(self._stop.wait())
            tasks = [*workers, drained, stopped]
            try:
                await asyncio.wait({drained, stopped}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._running = False
            self.result.elapsed = time.monotonic() - started
        return self.result

    async def _configure_crawl_delay(self) -> None:
        if self._robots is None:
            return
        crawl_delay = await self._robots.crawl_delay(self._site_url)
        if crawl_delay is None or crawl_delay <= self._delay:
            return
        if crawl_delay > MAX_CRAWL_DELAY:
            logger.warning(
                "robots.txt crawl-delay of %.0fs is unreasonable; using %.0fs",
                crawl_delay,
                MAX_CRAWL_DELAY,
            )
            crawl_delay = max(MAX_CRAWL_DELAY, self._delay)
        logger.info("using robots.txt crawl-delay of %.1fs", crawl_delay)
        self._delay = crawl_delay

    def _key(self, url: str) -> str:
        return normalize_url(url, ignore_www=self.config.ignore_www)

    def _is_on_site(self, url: str) -> bool:
        return is_same_site(url, self._site_url, ignore_www=self.config.ignore_www)

    def _enqueue(self, url: str, depth: int) -> None:
        if not is_http_url(url) or not self._is_on_site(url):
            return
        if self.config.max_depth is not None and depth > self.config.max_depth:
            return
        key = self._key(url)
        if key in self._seen:
            return
        self._seen.add(key)
        self._queue.put_nowait((url, depth))

    async def _worker(self) -> None:
        while True:
            url, depth = await self._queue.get()
            try:
                await self._process(url, depth)
            except Exception as exc:  # never let one page kill a worker
                logger.exception("unexpected error while crawling %s", url)
                self._record_failure(url, _describe(exc), None)
            finally:
                self._queue.task_done()

    async def _process(self, url: str, depth: int) -> None:
        # Reserve one of max_pages slots. Failed pages give their slot back, so
        # the report ends up with exactly max_pages pages when enough exist.
        async with self._slots:
            await self._slots.wait_for(
                lambda: (
                    self._stop.is_set()
                    or len(self.result.pages) + self._in_flight < self.config.max_pages
                )
            )
            if self._stop.is_set():
                return
            self._in_flight += 1

        try:
            await self._visit(url, depth)
        finally:
            async with self._slots:
                self._in_flight -= 1
                if len(self.result.pages) >= self.config.max_pages and not self._stop.is_set():
                    logger.info("reached maximum number of pages (%d)", self.config.max_pages)
                    self._stop.set()
                self._slots.notify_all()

    async def _visit(self, url: str, depth: int) -> None:
        try:
            response = await self._fetch_page(url, is_start=depth == 0)
        except SkipURL as exc:
            logger.info("skipping %s: %s", url, exc)
            self.result.skipped.append({"url": url, "reason": str(exc)})
            return
        except CrawlError as exc:
            self._record_failure(url, str(exc), exc.status_code)
            return
        except (aiohttp.ClientError, TimeoutError) as exc:
            self._record_failure(url, _describe(exc), None)
            return
        except _AlreadySeen as exc:
            logger.debug("%s redirects to already-seen %s", url, exc)
            return

        if depth == 0 and not self._is_on_site(response.url):
            # The start URL redirected to another host: crawl that site instead.
            logger.info("start URL redirected to %s; crawling that site", response.url)
            self._site_url = response.url
            await self._configure_crawl_delay()

        final_key = self._key(response.url)
        assert response.html is not None
        parsed = await asyncio.to_thread(
            extract_page_data, response.html, response.url, ignore_www=self.config.ignore_www
        )
        self.result.pages[final_key] = {**parsed, "status_code": response.status, "depth": depth}

        for link in parsed["internal_links"]:
            self._enqueue(link, depth + 1)

    def _record_failure(self, url: str, error: str, status_code: int | None) -> None:
        logger.warning("failed to crawl %s: %s", url, error)
        self.result.failures.append({"url": url, "status_code": status_code, "error": error})

    async def _fetch_page(self, url: str, *, is_start: bool = False) -> _Response:
        """Fetch an HTML page, following same-site redirects one hop at a time.

        Redirect targets are claimed in ``_seen`` before they are requested, so a
        page reachable both directly and via a redirect is only downloaded once.
        The start URL may redirect to another host (e.g. example.com -> www.example.com).
        """
        current = url
        chain_keys = {self._key(url)}
        for _ in range(MAX_REDIRECTS + 1):
            if self._robots is not None and not await self._robots.can_fetch(current):
                raise SkipURL(f"disallowed by robots.txt: {current}")
            response = await self._get(current, self._read_page)
            if response.status not in REDIRECT_STATUSES:
                return response
            if not response.location:
                raise RedirectError("redirect without a Location header", response.status)
            try:
                target = strip_fragment(urljoin(current, response.location))
            except ValueError:
                target = response.location
            if not is_http_url(target):
                raise RedirectError(f"redirect to unsupported URL {target}", response.status)
            if not is_start and not self._is_on_site(target):
                raise SkipURL(f"redirects off-site to {target}")
            key = self._key(target)
            # Same key as an earlier hop (http -> https, /a -> /a/): keep following.
            if key not in chain_keys:
                if key in self._seen:
                    raise _AlreadySeen(target)
                self._seen.add(key)
                chain_keys.add(key)
            current = target
        raise RedirectError(f"more than {MAX_REDIRECTS} redirects")

    async def _get[T](
        self,
        url: str,
        read: Callable[[str, aiohttp.ClientResponse], Awaitable[T]],
        *,
        allow_redirects: bool = False,
    ) -> T:
        """GET one URL and pass the response to ``read``, retrying transient failures."""
        retries = self.config.max_retries
        for attempt in range(retries + 1):
            retry_after: str | None = None
            await self._throttle()
            logger.info("fetching: %s", url)
            try:
                async with self.session.get(
                    url,
                    allow_redirects=allow_redirects,
                    headers={"User-Agent": self.config.user_agent},
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                ) as response:
                    if response.status not in RETRYABLE_STATUSES or attempt == retries:
                        return await read(url, response)
                    retry_after = response.headers.get("Retry-After")
                    reason = f"HTTP {response.status}"
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt == retries:
                    raise
                reason = type(exc).__name__
            wait = self._retry_delay(attempt, retry_after)
            logger.info("retrying %s in %.1fs (%s)", url, wait, reason)
            await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    async def _read_page(self, url: str, response: aiohttp.ClientResponse) -> _Response:
        status = response.status
        if status in REDIRECT_STATUSES:
            return _Response(url, status, location=response.headers.get("Location"))
        if status >= 400:
            raise HTTPStatusError(status)
        if response.content_type not in HTML_CONTENT_TYPES:
            raise NotHTMLError(response.content_type, status)
        body = await _read_limited(response, self.config.max_response_bytes)
        return _Response(url, status, html=decode_html(body, response.charset))

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        wait = self.config.retry_backoff * 2.0**attempt
        if retry_after and retry_after.strip().isdigit():
            wait = max(wait, min(float(retry_after), MAX_RETRY_AFTER))
        return wait

    async def _throttle(self) -> None:
        """Space out request starts by the politeness delay."""
        if self._delay <= 0:
            return
        async with self._throttle_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_request_at - now
            self._next_request_at = max(now, self._next_request_at) + self._delay
        if wait > 0:
            await asyncio.sleep(wait)

    async def _fetch_text(self, url: str) -> tuple[int, str]:
        """Fetch robots.txt (redirects followed, transient failures retried)."""
        return await self._get(url, self._read_text, allow_redirects=True)

    @staticmethod
    async def _read_text(url: str, response: aiohttp.ClientResponse) -> tuple[int, str]:
        if response.status >= 400:
            return response.status, ""
        # RFC 9309: parse at least the first 500 KiB; the file is UTF-8.
        body = await _read_limited(response, ROBOTS_MAX_BYTES, truncate=True)
        return response.status, body.decode("utf-8", errors="replace")


async def crawl_site(base_url: str, config: CrawlConfig | None = None) -> CrawlResult:
    """Crawl ``base_url`` with a fresh session and return the result."""
    async with AsyncCrawler(base_url, config) as crawler:
        return await crawler.crawl()
