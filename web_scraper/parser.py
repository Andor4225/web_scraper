"""HTML extraction helpers.

Each page is parsed once with :func:`make_soup`; the ``*_from_html`` helpers are
convenience wrappers for callers that only have a string.
"""

from typing import TypedDict
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from bs4.exceptions import FeatureNotFound

from .urls import is_http_url, is_same_site, strip_fragment


class ParsedPage(TypedDict):
    url: str
    heading: str
    first_paragraph: str
    outgoing_links: list[str]
    internal_links: list[str]
    external_links: list[str]
    image_urls: list[str]


def make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:  # lxml is a dependency, but degrade gracefully without it
        return BeautifulSoup(html, "html.parser")


def _attr(tag: Tag, name: str) -> str:
    value = tag.get(name)
    if isinstance(value, list):  # multi-valued attributes come back as lists
        value = " ".join(value)
    return (value or "").strip()


def get_base_url(soup: BeautifulSoup, page_url: str) -> str:
    """Return the URL relative links resolve against, honouring ``<base href>``."""
    base = soup.find("base", href=True)
    if isinstance(base, Tag) and (href := _attr(base, "href")):
        try:
            return urljoin(page_url, href)
        except ValueError:
            pass
    return page_url


def _resolve_all(soup: BeautifulSoup, page_url: str, tag_name: str, attr: str) -> list[str]:
    base_url = get_base_url(soup, page_url)
    seen: set[str] = set()
    urls: list[str] = []
    for tag in soup.find_all(tag_name):
        if not isinstance(tag, Tag):
            continue
        raw = _attr(tag, attr)
        if not raw:
            continue
        try:
            url = strip_fragment(urljoin(base_url, raw))
        except ValueError:  # malformed href (e.g. "http://[bad"): skip just this link
            continue
        # Drops mailto:, javascript:, tel:, data: and other non-HTTP targets.
        if not is_http_url(url) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def heading_from_soup(soup: BeautifulSoup) -> str:
    heading_tag = soup.find("h1") or soup.find("h2")
    return heading_tag.get_text(strip=True) if isinstance(heading_tag, Tag) else ""


def first_paragraph_from_soup(soup: BeautifulSoup) -> str:
    main_tag = soup.find("main")
    if isinstance(main_tag, Tag):
        main_p = main_tag.find("p")
        if isinstance(main_p, Tag):
            return main_p.get_text(strip=True)
    first_p = soup.find("p")
    return first_p.get_text(strip=True) if isinstance(first_p, Tag) else ""


def urls_from_soup(soup: BeautifulSoup, page_url: str) -> list[str]:
    return _resolve_all(soup, page_url, "a", "href")


def images_from_soup(soup: BeautifulSoup, page_url: str) -> list[str]:
    return _resolve_all(soup, page_url, "img", "src")


def get_heading_from_html(html: str) -> str:
    return heading_from_soup(make_soup(html))


def get_first_paragraph_from_html(html: str) -> str:
    return first_paragraph_from_soup(make_soup(html))


def get_urls_from_html(html: str, base_url: str) -> list[str]:
    return urls_from_soup(make_soup(html), base_url)


def get_images_from_html(html: str, base_url: str) -> list[str]:
    return images_from_soup(make_soup(html), base_url)


def extract_page_data(html: str, page_url: str, *, ignore_www: bool = True) -> ParsedPage:
    """Parse ``html`` once and extract everything the report needs."""
    soup = make_soup(html)
    links = urls_from_soup(soup, page_url)
    internal = [u for u in links if is_same_site(u, page_url, ignore_www=ignore_www)]
    internal_set = set(internal)
    return {
        "url": page_url,
        "heading": heading_from_soup(soup),
        "first_paragraph": first_paragraph_from_soup(soup),
        "outgoing_links": links,
        "internal_links": internal,
        "external_links": [u for u in links if u not in internal_set],
        "image_urls": images_from_soup(soup, page_url),
    }
