"""cover_url validation / SSRF-guard tests (B1.8)."""

import pytest

from app.utils.url_safety import validate_cover_url, _host_allowed, _is_public_ip


def test_allows_known_cover_hosts():
    assert validate_cover_url("https://books.google.com/books/content?id=x") is not None
    assert validate_cover_url("https://covers.openlibrary.org/b/id/123-M.jpg") is not None
    assert validate_cover_url("https://lh3.googleusercontent.com/abc") is not None


def test_none_and_empty_pass_through():
    assert validate_cover_url(None) is None
    assert validate_cover_url("   ") is None


@pytest.mark.parametrize("bad", [
    "http://books.google.com/x",          # not https
    "https://169.254.169.254/latest/meta",  # cloud metadata
    "https://localhost:8100/api/admin",     # internal service
    "https://evil.example.com/cover.jpg",   # not allowlisted
    "https://books.google.com.evil.com/x",  # suffix-spoof attempt
    "file:///etc/passwd",
])
def test_rejects_unsafe_urls(bad):
    with pytest.raises(ValueError):
        validate_cover_url(bad)


def test_host_allow_is_suffix_anchored():
    assert _host_allowed("books.google.com")
    assert _host_allowed("x.googleusercontent.com")
    assert not _host_allowed("googleusercontent.com.attacker.net")
    assert not _host_allowed("notgoogle.com")


def test_private_ips_are_not_public():
    assert not _is_public_ip("127.0.0.1")
    assert not _is_public_ip("169.254.169.254")
    assert not _is_public_ip("10.0.0.5")
    assert not _is_public_ip("::1")
    assert _is_public_ip("8.8.8.8")
