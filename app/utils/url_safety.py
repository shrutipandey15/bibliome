"""URL safety for user-supplied book cover images (B1.8 / P0-3).

Two layers of defense:
  1. `validate_cover_url` — enforced on write (https + host allowlist), so an
     attacker can't store an internal address like http://169.254.169.254/ in
     `cover_url` in the first place.
  2. `fetch_cover_safely` — enforced at fetch time on the unauthenticated
     story-image endpoint: re-validates the allowlist, resolves DNS and rejects
     any private/loopback/link-local target (DNS-rebinding guard), refuses
     redirects, caps the body size, and requires an image content-type.

Covers legitimately come only from Google Books and Open Library.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("bibliome.url_safety")

# Hosts trusted to serve book covers. Suffix-matched: host == suffix or a subdomain of it.
ALLOWED_COVER_HOST_SUFFIXES = (
    "books.google.com",
    "googleusercontent.com",   # Google Books sometimes serves via *.googleusercontent.com
    "covers.openlibrary.org",
)

MAX_COVER_BYTES = 5 * 1024 * 1024  # 5 MB


def _host_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in ALLOWED_COVER_HOST_SUFFIXES)


def validate_cover_url(url: str | None) -> str | None:
    """Return the URL if it is a safe cover source, else raise ValueError.

    None/empty is allowed (covers are optional). Used by the entry schema so
    invalid URLs are rejected at the edge (422) rather than stored.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("cover_url must be an https URL")
    if not parsed.hostname or not _host_allowed(parsed.hostname):
        raise ValueError("cover_url host is not an allowed book-cover source")
    return url


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def fetch_cover_safely(url: str | None) -> bytes | None:
    """SSRF-hardened fetch of a book cover. Returns bytes, or None on any
    violation/failure (callers treat a missing cover as non-fatal)."""
    try:
        validate_cover_url(url)
    except ValueError:
        return None
    assert url is not None

    host = urlparse(url).hostname
    # Reject if DNS resolves to any non-public address (DNS-rebinding guard).
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    if not infos or any(not _is_public_ip(info[4][0]) for info in infos):
        logger.warning("Refusing cover fetch: %s resolves to a non-public address", host)
        return None

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=5.0) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                if not resp.headers.get("content-type", "").startswith("image/"):
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_COVER_BYTES:
                        logger.warning("Cover exceeded %d bytes, aborting: %s", MAX_COVER_BYTES, url)
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
    except Exception as e:
        logger.warning("Cover fetch failed for %s: %s", url, e)
        return None
