import csv
import io
import json
from pathlib import Path

import pytest

from web_scraper.crawler import CrawlResult, PageData
from web_scraper.report import infer_format, render_csv, render_json, write_report


def _page(url: str, **overrides: object) -> PageData:
    page: PageData = {
        "url": url,
        "heading": "Title",
        "first_paragraph": "Intro",
        "outgoing_links": [f"{url}/a", "https://example.org/"],
        "internal_links": [f"{url}/a"],
        "external_links": ["https://example.org/"],
        "image_urls": [f"{url}/logo.png"],
        "status_code": 200,
        "depth": 0,
    }
    page.update(overrides)  # type: ignore[typeddict-item]
    return page


@pytest.fixture
def result() -> CrawlResult:
    return CrawlResult(
        base_url="https://site.test",
        pages={
            "site.test/b": _page("https://site.test/b", depth=1),
            "site.test": _page("https://site.test", heading='Quotes "and", commas'),
        },
        failures=[{"url": "https://site.test/missing", "status_code": 404, "error": "HTTP 404"}],
        skipped=[{"url": "https://site.test/private", "reason": "disallowed by robots.txt"}],
        elapsed=1.23456,
    )


def test_render_json(result: CrawlResult) -> None:
    report = json.loads(render_json(result))
    assert report["summary"]["pages_crawled"] == 2
    assert report["summary"]["pages_failed"] == 1
    assert report["summary"]["pages_skipped"] == 1
    assert report["summary"]["elapsed_seconds"] == 1.235
    assert report["summary"]["interrupted"] is False
    assert [p["url"] for p in report["pages"]] == ["https://site.test", "https://site.test/b"]
    assert report["failures"][0]["status_code"] == 404
    assert report["skipped"][0]["reason"] == "disallowed by robots.txt"


def test_render_json_keeps_unicode() -> None:
    result = CrawlResult(base_url="https://site.test", pages={"k": _page("https://site.test")})
    result.pages["k"]["heading"] = "Café ☕"
    assert "Café ☕" in render_json(result)


def test_render_csv(result: CrawlResult) -> None:
    rows = list(csv.DictReader(io.StringIO(render_csv(result))))
    assert [r["url"] for r in rows] == [
        "https://site.test",
        "https://site.test/b",
        "https://site.test/missing",
    ]
    assert rows[0]["heading"] == 'Quotes "and", commas'
    assert rows[0]["internal_link_count"] == "1"
    assert rows[0]["external_links"] == "https://example.org/"
    assert rows[1]["depth"] == "1"
    assert rows[2]["status_code"] == "404"
    assert rows[2]["error"] == "HTTP 404"


@pytest.mark.parametrize(
    ("name", "fmt"), [("r.csv", "csv"), ("R.CSV", "csv"), ("r.json", "json"), ("r", "json")]
)
def test_infer_format(name: str, fmt: str) -> None:
    assert infer_format(name) == fmt


def test_write_report_json(tmp_path: Path, result: CrawlResult) -> None:
    out = write_report(result, tmp_path / "nested" / "report.json")
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["pages_crawled"] == 2
    assert list(out.parent.iterdir()) == [out]  # no temp files left behind


def test_write_report_format_override(tmp_path: Path, result: CrawlResult) -> None:
    out = write_report(result, tmp_path / "report.txt", "csv")
    assert out.read_text(encoding="utf-8").startswith("url,status_code,")


def test_write_report_failure_keeps_existing_file(
    tmp_path: Path, result: CrawlResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "report.json"
    out.write_text("previous", encoding="utf-8")

    def boom(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("web_scraper.report.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_report(result, out)
    assert out.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [out]
