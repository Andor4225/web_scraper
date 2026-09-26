"""Command-line interface: ``web-scraper URL [options]``."""

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from .crawler import DEFAULT_USER_AGENT, AsyncCrawler, CrawlConfig, CrawlResult
from .report import ReportFormat, infer_format, write_report
from .urls import is_http_url

logger = logging.getLogger("web_scraper")

EXIT_OK = 0
EXIT_NO_PAGES = 1
EXIT_INTERRUPTED = 130


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def _non_negative_int(value: str) -> int:
    if value.isdigit():
        return int(value)
    raise argparse.ArgumentTypeError(f"expected a whole number >= 0, got {value!r}")


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if number < 0 or number != number:  # also rejects NaN
        raise argparse.ArgumentTypeError(f"must not be negative, got {value}")
    return number


def _positive_float(value: str) -> float:
    number = _non_negative_float(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def _url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = f"https://{value}"
    if not is_http_url(value):
        raise argparse.ArgumentTypeError(f"not a valid http(s) URL: {value!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web-scraper",
        description="Crawl a website (staying on its domain) and export page metadata.",
    )
    parser.add_argument("url", type=_url, help="start URL; https:// is assumed if omitted")
    # Legacy positional form: `main.py URL [CONCURRENCY] [MAX_PAGES]`.
    parser.add_argument("legacy_concurrency", nargs="?", type=_positive_int, help=argparse.SUPPRESS)
    parser.add_argument("legacy_max_pages", nargs="?", type=_positive_int, help=argparse.SUPPRESS)

    crawl = parser.add_argument_group("crawl")
    crawl.add_argument(
        "-c", "--concurrency", type=_positive_int, help="parallel requests (default: 3)"
    )
    crawl.add_argument(
        "-n", "--max-pages", type=_positive_int, help="pages to crawl successfully (default: 10)"
    )
    crawl.add_argument(
        "-d", "--max-depth", type=_non_negative_int, help="max link depth from the start URL"
    )
    crawl.add_argument(
        "--delay",
        type=_non_negative_float,
        default=0.0,
        help="seconds between requests (robots.txt crawl-delay wins if larger)",
    )
    crawl.add_argument(
        "--timeout", type=_positive_float, default=15.0, help="per-request timeout in seconds"
    )
    crawl.add_argument(
        "--retries", type=_non_negative_int, default=2, help="retries for 429/5xx/network errors"
    )
    crawl.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent header")
    crawl.add_argument(
        "--ignore-robots", action="store_true", help="do not fetch or obey robots.txt"
    )
    crawl.add_argument(
        "--strict-host",
        action="store_true",
        help="treat www.example.com and example.com as different sites",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "-o", "--output", type=Path, help="report path (default: report.json / report.csv)"
    )
    output.add_argument(
        "-f", "--format", choices=["json", "csv"], help="report format (default: from extension)"
    )
    verbosity = output.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    verbosity.add_argument("-q", "--quiet", action="store_true", help="only log errors")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> tuple[str, CrawlConfig, Path, ReportFormat]:
    parser = build_parser()
    args = parser.parse_args(argv)

    concurrency = args.concurrency or args.legacy_concurrency or 3
    max_pages = args.max_pages or args.legacy_max_pages or 10
    config = CrawlConfig(
        max_concurrency=concurrency,
        max_pages=max_pages,
        max_depth=args.max_depth,
        timeout=args.timeout,
        max_retries=args.retries,
        delay=args.delay,
        user_agent=args.user_agent,
        respect_robots=not args.ignore_robots,
        ignore_www=not args.strict_host,
    )

    fmt: ReportFormat = args.format or (infer_format(args.output) if args.output else "json")
    output = args.output or Path(f"report.{fmt}")

    level = logging.DEBUG if args.verbose else logging.ERROR if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    return args.url, config, output, fmt


def _install_sigint_handler(crawler: AsyncCrawler) -> None:
    """First Ctrl+C stops gracefully and keeps partial results; a second one aborts."""
    loop = asyncio.get_running_loop()

    def on_sigint() -> None:
        logger.warning("interrupted: finishing up (press Ctrl+C again to abort)")
        loop.remove_signal_handler(signal.SIGINT)
        crawler.request_stop()

    # Unsupported on Windows and outside the main thread; Ctrl+C then aborts.
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGINT, on_sigint)


async def _crawl(url: str, config: CrawlConfig) -> CrawlResult:
    async with AsyncCrawler(url, config) as crawler:
        _install_sigint_handler(crawler)
        return await crawler.crawl()


def print_summary(result: CrawlResult, output: Path) -> None:
    status = " (interrupted)" if result.interrupted else ""
    print(
        f"crawled {len(result.pages)} page(s), {len(result.failures)} failed, "
        f"{len(result.skipped)} skipped in {result.elapsed:.1f}s{status}"
    )
    print(f"report written to {output}")


def main(argv: Sequence[str] | None = None) -> int:
    url, config, output, fmt = parse_args(argv)
    logger.info(
        "starting crawl of %s (concurrency: %d, max pages: %d)",
        url,
        config.max_concurrency,
        config.max_pages,
    )
    try:
        result = asyncio.run(_crawl(url, config))
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return EXIT_INTERRUPTED

    write_report(result, output, fmt)
    print_summary(result, output)
    if result.interrupted:
        return EXIT_INTERRUPTED
    return EXIT_OK if result.pages else EXIT_NO_PAGES


def run() -> None:
    sys.exit(main())
