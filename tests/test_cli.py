import json
from pathlib import Path

import pytest

from web_scraper import cli
from web_scraper.crawler import CrawlConfig, CrawlResult


def test_defaults() -> None:
    url, config, output, fmt = cli.parse_args(["https://example.com"])
    assert url == "https://example.com"
    assert config.max_concurrency == 3
    assert config.max_pages == 10
    assert config.respect_robots is True
    assert output == Path("report.json")
    assert fmt == "json"


def test_scheme_is_added_when_missing() -> None:
    url, *_ = cli.parse_args(["example.com/docs"])
    assert url == "https://example.com/docs"


def test_legacy_positional_arguments() -> None:
    _, config, *_ = cli.parse_args(["https://example.com", "5", "20"])
    assert (config.max_concurrency, config.max_pages) == (5, 20)


def test_flags_override_legacy_positionals() -> None:
    _, config, *_ = cli.parse_args(["https://example.com", "5", "20", "-c", "2", "-n", "7"])
    assert (config.max_concurrency, config.max_pages) == (2, 7)


def test_all_options() -> None:
    _, config, output, fmt = cli.parse_args(
        [
            "https://example.com",
            "--max-depth", "2",
            "--delay", "0.5",
            "--timeout", "3",
            "--retries", "0",
            "--user-agent", "bot/1",
            "--ignore-robots",
            "--strict-host",
            "-o", "out/site.csv",
        ]
    )  # fmt: skip
    assert config == CrawlConfig(
        max_depth=2,
        delay=0.5,
        timeout=3.0,
        max_retries=0,
        user_agent="bot/1",
        respect_robots=False,
        ignore_www=False,
    )
    assert output == Path("out/site.csv")
    assert fmt == "csv"


def test_csv_format_picks_default_filename() -> None:
    *_, output, fmt = cli.parse_args(["https://example.com", "-f", "csv"])
    assert (output, fmt) == (Path("report.csv"), "csv")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["ftp://example.com"],
        ["https://"],
        ["https://example.com", "abc"],
        ["https://example.com", "0"],
        ["https://example.com", "-n", "-1"],
        ["https://example.com", "--delay", "-1"],
        ["https://example.com", "--delay", "nan"],
        ["https://example.com", "--timeout", "0"],
        ["https://example.com", "--max-depth", "x"],
        ["https://example.com", "-f", "xml"],
        ["https://example.com", "-v", "-q"],
        ["https://example.com", "1", "2", "3"],
    ],
)
def test_invalid_arguments_exit_with_usage_error(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def _fake_crawl(result: CrawlResult):  # type: ignore[no-untyped-def]
    async def crawl(url: str, config: CrawlConfig) -> CrawlResult:
        return result

    return crawl


def test_main_writes_report_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = CrawlResult(base_url="https://example.com", elapsed=0.5)
    result.pages["example.com"] = {
        "url": "https://example.com",
        "heading": "Hi",
        "first_paragraph": "",
        "outgoing_links": [],
        "internal_links": [],
        "external_links": [],
        "image_urls": [],
        "status_code": 200,
        "depth": 0,
    }
    monkeypatch.setattr(cli, "_crawl", _fake_crawl(result))
    out = tmp_path / "r.json"

    assert cli.main(["https://example.com", "-o", str(out), "-q"]) == cli.EXIT_OK
    assert json.loads(out.read_text())["pages"][0]["heading"] == "Hi"
    stdout = capsys.readouterr().out
    assert "crawled 1 page(s), 0 failed, 0 skipped" in stdout
    assert str(out) in stdout


def test_main_exit_code_when_nothing_crawled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_crawl", _fake_crawl(CrawlResult(base_url="https://example.com")))
    argv = ["https://example.com", "-o", str(tmp_path / "r.json"), "-q"]
    assert cli.main(argv) == cli.EXIT_NO_PAGES


def test_main_interrupted_still_writes_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = CrawlResult(base_url="https://example.com", interrupted=True)
    monkeypatch.setattr(cli, "_crawl", _fake_crawl(result))
    out = tmp_path / "r.json"
    assert cli.main(["https://example.com", "-o", str(out), "-q"]) == cli.EXIT_INTERRUPTED
    assert json.loads(out.read_text())["summary"]["interrupted"] is True


def test_int_options_share_parsing_rules() -> None:
    _, config, *_ = cli.parse_args(["https://example.com", "--retries", "+2", "-c", "+2"])
    assert (config.max_retries, config.max_concurrency) == (2, 2)


def test_non_ascii_digits_rejected_with_clear_message(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["https://example.com", "--retries", "\u00b2"])
    assert "expected a whole number" in capsys.readouterr().err
