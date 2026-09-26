"""Per-origin robots.txt cache.

Follows RFC 9309: 4xx means no restrictions, 5xx means disallow everything.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

# Fetches a URL and returns (status, body); raises on network errors.
Fetcher = Callable[[str], Awaitable[tuple[int, str]]]


class RobotsCache:
    def __init__(self, fetch: Fetcher, user_agent: str) -> None:
        self._fetch = fetch
        self._user_agent = user_agent
        # A parser, or True/False meaning "allow everything"/"disallow everything".
        self._rules: dict[str, RobotFileParser | bool] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"

    async def _rules_for(self, url: str) -> RobotFileParser | bool:
        origin = self._origin(url)
        if origin in self._rules:
            return self._rules[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._rules:
                self._rules[origin] = await self._load(origin)
            return self._rules[origin]

    async def _load(self, origin: str) -> RobotFileParser | bool:
        robots_url = f"{origin}/robots.txt"
        try:
            status, body = await self._fetch(robots_url)
        except Exception as exc:
            # Network failure: the page request itself will fail and be reported
            # with the real error, which is more useful than a robots.txt skip.
            logger.warning("could not fetch %s (%s)", robots_url, exc)
            return True

        if 200 <= status < 300:
            parser = RobotFileParser(robots_url)
            parser.parse(body.splitlines())
            return parser
        if 400 <= status < 500:
            return True  # "unavailable": no restrictions
        logger.warning("%s returned HTTP %d; treating site as disallowed", robots_url, status)
        return False

    async def can_fetch(self, url: str) -> bool:
        rules = await self._rules_for(url)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(self._user_agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        rules = await self._rules_for(url)
        if isinstance(rules, bool):
            return None
        delay = rules.crawl_delay(self._user_agent)
        return float(delay) if delay is not None else None
