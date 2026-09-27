"""URL normalization and same-site checks."""

from urllib.parse import SplitResult, urldefrag, urlsplit

HTTP_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def safe_urlsplit(url: str) -> SplitResult | None:
    """``urlsplit`` that returns None instead of raising on malformed URLs."""
    try:
        return urlsplit(url)
    except ValueError:  # e.g. an unclosed IPv6 bracket: "http://[bad"
        return None


def _host_port(url: str) -> tuple[str, int | None]:
    """Return the lowercased host and the port, dropping the scheme's default port."""
    parts = safe_urlsplit(url)
    if parts is None:
        return "", None
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:  # malformed port such as "host:abc"
        port = None
    if port is not None and port == _DEFAULT_PORTS.get(parts.scheme.lower()):
        port = None
    return host, port


def normalize_url(url: str, *, ignore_www: bool = False) -> str:
    """Return a scheme-less key used to deduplicate URLs.

    The host is lowercased, default ports and the fragment are dropped, a
    trailing slash is removed, and the query string is kept because it often
    identifies a different page (e.g. ``?page=2``). With ``ignore_www`` a
    leading ``www.`` is dropped too, matching :func:`is_same_site`.
    """
    parts = safe_urlsplit(url)
    if parts is None:
        return url
    host, port = _host_port(url)
    if ignore_www and host.startswith("www."):
        host = host[4:]
    netloc = f"{host}:{port}" if port is not None else host
    key = f"{netloc}{parts.path}".rstrip("/")
    if parts.query:
        key = f"{key}?{parts.query}"
    return key


def site_key(url: str, *, ignore_www: bool = True) -> str:
    """Return the identity of the site a URL belongs to (host plus non-default port)."""
    host, port = _host_port(url)
    if ignore_www and host.startswith("www."):
        host = host[4:]
    return f"{host}:{port}" if port is not None else host


def is_same_site(url: str, other: str, *, ignore_www: bool = True) -> bool:
    """Return True when both URLs belong to the same site."""
    return site_key(url, ignore_www=ignore_www) == site_key(other, ignore_www=ignore_www)


def is_http_url(url: str) -> bool:
    parts = safe_urlsplit(url)
    if parts is None:
        return False
    try:
        return parts.scheme.lower() in HTTP_SCHEMES and bool(parts.hostname)
    except ValueError:
        return False


def strip_fragment(url: str) -> str:
    return urldefrag(url).url
