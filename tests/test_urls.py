import pytest

from web_scraper.urls import is_http_url, is_same_site, normalize_url, site_key


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.boot.dev/blog/path", "www.boot.dev/blog/path"),
        ("http://www.boot.dev/blog/path", "www.boot.dev/blog/path"),
        ("https://www.boot.dev/blog/path/", "www.boot.dev/blog/path"),
        ("https://www.boot.dev/", "www.boot.dev"),
        ("https://www.boot.dev", "www.boot.dev"),
        # Host is case-insensitive; path is not.
        ("https://WWW.Boot.DEV/Blog", "www.boot.dev/Blog"),
        # Default ports are dropped, others are kept.
        ("https://boot.dev:443/a", "boot.dev/a"),
        ("http://boot.dev:80/a", "boot.dev/a"),
        ("http://boot.dev:8080/a", "boot.dev:8080/a"),
        # Fragments are dropped, query strings are kept.
        ("https://boot.dev/a#section", "boot.dev/a"),
        ("https://boot.dev/list?page=2", "boot.dev/list?page=2"),
        ("https://boot.dev/list/?page=2", "boot.dev/list?page=2"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


def test_query_pages_are_distinct() -> None:
    assert normalize_url("https://x.com/list?page=1") != normalize_url("https://x.com/list?page=2")


def test_site_key() -> None:
    assert site_key("https://WWW.Example.com/a") == "example.com"
    assert site_key("https://www.example.com/a", ignore_www=False) == "www.example.com"
    assert site_key("http://example.com:8080/") == "example.com:8080"


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("https://example.com/a", "https://example.com/b", True),
        ("https://Example.COM/a", "https://example.com/b", True),
        ("https://www.example.com/", "https://example.com/", True),
        ("http://example.com/", "https://example.com/", True),
        ("https://example.com:443/", "https://example.com/", True),
        ("https://example.com:8443/", "https://example.com/", False),
        ("https://blog.example.com/", "https://example.com/", False),
        ("https://example.org/", "https://example.com/", False),
    ],
)
def test_is_same_site(a: str, b: str, same: bool) -> None:
    assert is_same_site(a, b) is same


def test_is_same_site_strict_www() -> None:
    assert not is_same_site("https://www.example.com/", "https://example.com/", ignore_www=False)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://example.com", True),
        ("HTTP://example.com/x", True),
        ("mailto:someone@example.com", False),
        ("javascript:alert(1)", False),
        ("ftp://example.com/", False),
        ("https://", False),
        ("/relative/path", False),
    ],
)
def test_is_http_url(url: str, ok: bool) -> None:
    assert is_http_url(url) is ok
