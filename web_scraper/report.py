"""Write crawl results to JSON or CSV."""

import csv
import io
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .crawler import CrawlResult

ReportFormat = Literal["json", "csv"]

CSV_FIELDS = [
    "url",
    "status_code",
    "depth",
    "heading",
    "first_paragraph",
    "internal_link_count",
    "external_link_count",
    "image_count",
    "internal_links",
    "external_links",
    "image_urls",
    "error",
]


def infer_format(path: str | Path) -> ReportFormat:
    return "csv" if Path(path).suffix.lower() == ".csv" else "json"


def summarize(result: CrawlResult) -> dict[str, Any]:
    return {
        "base_url": result.base_url,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pages_crawled": len(result.pages),
        "pages_failed": len(result.failures),
        "pages_skipped": len(result.skipped),
        "elapsed_seconds": round(result.elapsed, 3),
        "interrupted": result.interrupted,
    }


def _sorted_pages(result: CrawlResult) -> list[dict[str, Any]]:
    return [dict(page) for page in sorted(result.pages.values(), key=lambda p: p["url"])]


def render_json(result: CrawlResult) -> str:
    report = {
        "summary": summarize(result),
        "pages": _sorted_pages(result),
        "failures": sorted(result.failures, key=lambda f: f["url"]),
        "skipped": sorted(result.skipped, key=lambda s: s["url"]),
    }
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def render_csv(result: CrawlResult) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for page in _sorted_pages(result):
        writer.writerow(
            {
                "url": page["url"],
                "status_code": page["status_code"],
                "depth": page["depth"],
                "heading": page["heading"],
                "first_paragraph": page["first_paragraph"],
                "internal_link_count": len(page["internal_links"]),
                "external_link_count": len(page["external_links"]),
                "image_count": len(page["image_urls"]),
                # URLs never contain raw spaces, so a space is a safe separator.
                "internal_links": " ".join(page["internal_links"]),
                "external_links": " ".join(page["external_links"]),
                "image_urls": " ".join(page["image_urls"]),
                "error": "",
            }
        )
    for failure in sorted(result.failures, key=lambda f: f["url"]):
        writer.writerow(
            {
                "url": failure["url"],
                "status_code": failure["status_code"],
                "error": failure["error"],
            }
        )
    return buffer.getvalue()


def write_report(
    result: CrawlResult, path: str | Path = "report.json", fmt: ReportFormat | None = None
) -> Path:
    """Write the report atomically, so an interrupted write never leaves a truncated file."""
    path = Path(path)
    content = render_csv(result) if (fmt or infer_format(path)) == "csv" else render_json(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.chmod(tmp_name, 0o644)  # mkstemp creates 0600 files
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path
