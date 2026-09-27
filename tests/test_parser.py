from web_scraper.parser import (
    extract_page_data,
    get_first_paragraph_from_html,
    get_heading_from_html,
    get_images_from_html,
    get_urls_from_html,
)

BASE = "https://crawler-test.com"


# get_heading_from_html


def test_heading_basic() -> None:
    assert get_heading_from_html("<html><body><h1>Test Title</h1></body></html>") == "Test Title"


def test_heading_h2_fallback() -> None:
    html = "<html><body><h2>Fallback Subtitle</h2></body></html>"
    assert get_heading_from_html(html) == "Fallback Subtitle"


def test_heading_none() -> None:
    assert get_heading_from_html("<html><body><p>No header here</p></body></html>") == ""


# get_first_paragraph_from_html


def test_first_paragraph_main_priority() -> None:
    html = """<html><body>
        <p>Outside paragraph.</p>
        <main><p>Main paragraph.</p></main>
    </body></html>"""
    assert get_first_paragraph_from_html(html) == "Main paragraph."


def test_first_paragraph_fallback() -> None:
    html = "<html><body><p>First paragraph.</p></body></html>"
    assert get_first_paragraph_from_html(html) == "First paragraph."


def test_first_paragraph_none() -> None:
    assert get_first_paragraph_from_html("<html><body><div>Just a div</div></body></html>") == ""


# get_urls_from_html


def test_urls_absolute() -> None:
    html = '<a href="https://crawler-test.com"><span>Boot.dev</span></a>'
    assert get_urls_from_html(html, BASE) == ["https://crawler-test.com"]


def test_urls_relative() -> None:
    html = '<a href="/path/to/page">Link</a>'
    assert get_urls_from_html(html, BASE) == ["https://crawler-test.com/path/to/page"]


def test_urls_no_href() -> None:
    assert get_urls_from_html("<a>Broken link</a>", BASE) == []


def test_urls_deduplicated_and_fragments_stripped() -> None:
    html = '<a href="/a">1</a><a href="/a">2</a><a href="/a#top">3</a><a href="#top">4</a>'
    assert get_urls_from_html(html, f"{BASE}/p") == [f"{BASE}/a", f"{BASE}/p"]


def test_urls_skip_non_http_schemes() -> None:
    html = """
        <a href="mailto:hi@example.com">mail</a>
        <a href="javascript:void(0)">js</a>
        <a href="tel:+123">tel</a>
        <a href="ftp://example.com/file">ftp</a>
        <a href="/ok">ok</a>
    """
    assert get_urls_from_html(html, BASE) == [f"{BASE}/ok"]


def test_urls_keep_query_string() -> None:
    html = '<a href="/list?page=1">1</a><a href="/list?page=2">2</a>'
    assert get_urls_from_html(html, BASE) == [f"{BASE}/list?page=1", f"{BASE}/list?page=2"]


def test_urls_whitespace_in_href_is_trimmed() -> None:
    assert get_urls_from_html('<a href="  /spaced  ">x</a>', BASE) == [f"{BASE}/spaced"]


def test_urls_honour_base_href() -> None:
    html = '<head><base href="/docs/"></head><body><a href="intro">Intro</a></body>'
    assert get_urls_from_html(html, f"{BASE}/other/page") == [f"{BASE}/docs/intro"]


# get_images_from_html


def test_images_relative() -> None:
    assert get_images_from_html('<img src="/logo.png" alt="Logo">', BASE) == [f"{BASE}/logo.png"]


def test_images_absolute() -> None:
    html = '<img src="https://cdn.crawler-test.com/banner.jpg">'
    assert get_images_from_html(html, BASE) == ["https://cdn.crawler-test.com/banner.jpg"]


def test_images_no_src() -> None:
    assert get_images_from_html('<img alt="Missing source">', BASE) == []


def test_images_skip_data_uris_and_dedupe() -> None:
    html = '<img src="data:image/png;base64,AAAA"><img src="/a.png"><img src="/a.png">'
    assert get_images_from_html(html, BASE) == [f"{BASE}/a.png"]


# extract_page_data


def test_extract_page_data_basic() -> None:
    html = """<html><body>
        <h1>Test Title</h1>
        <p>This is the first paragraph.</p>
        <a href="/link1">Link 1</a>
        <img src="/image1.jpg" alt="Image 1">
    </body></html>"""
    assert extract_page_data(html, BASE) == {
        "url": BASE,
        "heading": "Test Title",
        "first_paragraph": "This is the first paragraph.",
        "outgoing_links": [f"{BASE}/link1"],
        "internal_links": [f"{BASE}/link1"],
        "external_links": [],
        "image_urls": [f"{BASE}/image1.jpg"],
    }


def test_extract_page_data_empty() -> None:
    url = f"{BASE}/empty"
    assert extract_page_data("<html><body><div>Empty document</div></body></html>", url) == {
        "url": url,
        "heading": "",
        "first_paragraph": "",
        "outgoing_links": [],
        "internal_links": [],
        "external_links": [],
        "image_urls": [],
    }


def test_extract_page_data_complex() -> None:
    url = f"{BASE}/blog"
    html = """<html><body>
        <h2>Sub Heading</h2>
        <main><p>Lead content</p></main>
        <a href="/docs">Docs</a>
        <img src="/photo.webp" alt="Photo">
    </body></html>"""
    page = extract_page_data(html, url)
    assert page["heading"] == "Sub Heading"
    assert page["first_paragraph"] == "Lead content"
    assert page["outgoing_links"] == [f"{BASE}/docs"]
    assert page["image_urls"] == [f"{BASE}/photo.webp"]


def test_extract_page_data_splits_internal_and_external() -> None:
    html = """
        <a href="/about">About</a>
        <a href="https://www.crawler-test.com/team">Team</a>
        <a href="https://example.org/">Elsewhere</a>
    """
    page = extract_page_data(html, BASE)
    assert page["internal_links"] == [f"{BASE}/about", "https://www.crawler-test.com/team"]
    assert page["external_links"] == ["https://example.org/"]

    strict = extract_page_data(html, BASE, ignore_www=False)
    assert strict["external_links"] == ["https://www.crawler-test.com/team", "https://example.org/"]


def test_malformed_href_skips_only_that_link() -> None:
    html = '<h1>Kept</h1><a href="http://[bad">bad</a><a href="/ok">ok</a>'
    page = extract_page_data(html, BASE)
    assert page["heading"] == "Kept"
    assert page["outgoing_links"] == [f"{BASE}/ok"]


def test_malformed_base_href_falls_back_to_page_url() -> None:
    html = '<base href="http://[bad"><a href="ok">ok</a>'
    assert get_urls_from_html(html, f"{BASE}/dir/") == [f"{BASE}/dir/ok"]
