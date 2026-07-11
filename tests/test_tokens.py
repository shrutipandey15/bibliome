"""JWT token tests (B1.14 — PyJWT migration)."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import get_settings
from app.services.auth_service import (
    create_access_token,
    create_refresh_token_str,
    decode_token,
    hash_token,
)


def test_hash_token_is_deterministic_and_not_plaintext():
    tok = "some-refresh-token-value"
    assert hash_token(tok) == hash_token(tok)
    assert hash_token(tok) != tok
    assert len(hash_token(tok)) == 64  # sha256 hex
    assert hash_token("a") != hash_token("b")


def test_access_token_roundtrip():
    uid = uuid.uuid4()
    payload = decode_token(create_access_token(uid))
    assert payload is not None
    assert payload["sub"] == str(uid)
    assert payload["type"] == "access"


def test_refresh_token_has_unique_jti():
    uid = uuid.uuid4()
    t1, _ = create_refresh_token_str(uid)
    t2, _ = create_refresh_token_str(uid)
    assert decode_token(t1)["jti"] != decode_token(t2)["jti"]


def test_garbage_and_tampered_tokens_return_none():
    assert decode_token("not-a-jwt") is None
    assert decode_token(create_access_token(uuid.uuid4()) + "x") is None


def test_expired_token_rejected():
    settings = get_settings()
    expired = jwt.encode(
        {"sub": "x", "type": "access", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    assert decode_token(expired) is None


def test_wrong_algorithm_rejected():
    # A token signed with a different algorithm/key must not validate.
    forged = jwt.encode({"sub": "x", "type": "access"}, "someone-elses-key", algorithm="HS256")
    assert decode_token(forged) is None
