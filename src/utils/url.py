from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def canonical_event_url(url: str) -> str:
    """Единый вид ссылки для сравнения и хранения (без query/fragment)."""
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    if "afisha.yandex.ru" in netloc:
        return urlunparse((scheme, netloc, path, "", "", ""))
    return raw
