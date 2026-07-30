"""Journal wire types — the structure/content boundary made explicit.

Every crypto field is base64 and is validated for *shape only*: decodable, within
byte bounds, algorithm on the allowlist. Nothing here can tell whether a blob
decrypts, or whether it says anything at all — that's the point
(``journalCryptoContract.md`` §3).

Emotion tags reuse ``EmotionIn``/``EmotionOut`` from the book-entry schema
verbatim: same 18-slug vocabulary, same 1–10 strength, same legacy-slug
canonicalization on read. One vocabulary, one strength model, no journal dialect.
"""

import base64
import binascii
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.journal import JOURNAL_CIPHERS, JOURNAL_KDFS
from app.schemas.entry import EmotionIn, EmotionOut

Cipher = Literal["AES-GCM", "XChaCha20-Poly1305"]
Kdf = Literal["argon2id", "pbkdf2-sha256"]

# Byte bounds on the decoded values. Generous but finite — an unbounded ciphertext
# column is a free blob store, and a 4-byte "salt" is a client bug we can catch
# without ever seeing a key.
MAX_CIPHERTEXT_BYTES = 128 * 1024   # ~128 KB of prose per entry
MIN_NONCE_BYTES, MAX_NONCE_BYTES = 8, 32
MIN_SALT_BYTES, MAX_SALT_BYTES = 16, 64
MIN_WRAPPED_BYTES, MAX_WRAPPED_BYTES = 32, 160  # 32-byte DEK + AEAD tag + slack


def _b64_bytes(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{field} must be base64")


def _bounded_b64(value: str, field: str, lo: int, hi: int) -> str:
    n = len(_b64_bytes(value, field))
    if not lo <= n <= hi:
        raise ValueError(f"{field} must decode to {lo}–{hi} bytes (got {n})")
    return value


def _validate_ciphertext(v: str | None) -> str | None:
    """Shape check on the blob: base64, non-empty, bounded. Says nothing about
    whether it decrypts — nothing here could."""
    if v is None:
        return None
    n = len(_b64_bytes(v, "ciphertext"))
    if n == 0:
        raise ValueError("ciphertext must not be empty")
    if n > MAX_CIPHERTEXT_BYTES:
        raise ValueError(
            f"ciphertext must decode to at most {MAX_CIPHERTEXT_BYTES} bytes (got {n})"
        )
    return v


def _validate_nonce(v: str | None) -> str | None:
    if v is None:
        return None
    return _bounded_b64(v, "nonce", MIN_NONCE_BYTES, MAX_NONCE_BYTES)


class JournalKeyBundleIn(BaseModel):
    """The wrapped data-key, both ways. Sent at setup and on every re-wrap.

    The server persists this and hands it back. It never derives a key, never
    unwraps, and has no code path that could.
    """

    cipher: Cipher
    kdf: Kdf
    # Cost parameters, opaque to us: the client that derived the key is the only
    # party that needs to interpret them. Capped in size so it can't be abused as
    # a metadata dumping ground.
    kdf_params: dict = Field(default_factory=dict)

    password_salt: str = Field(max_length=200)
    wrapped_dek: str = Field(max_length=500)
    wrapped_dek_nonce: str = Field(max_length=100)

    # Mandatory. A journal with no recovery wrap is one password reset from being
    # permanently unopenable — we refuse to let a client set that up.
    recovery_salt: str = Field(max_length=200)
    wrapped_dek_recovery: str = Field(max_length=500)
    wrapped_dek_recovery_nonce: str = Field(max_length=100)

    key_version: int = Field(default=1, ge=1)

    @field_validator("password_salt", "recovery_salt")
    @classmethod
    def _check_salt(cls, v, info):
        return _bounded_b64(v, info.field_name, MIN_SALT_BYTES, MAX_SALT_BYTES)

    @field_validator("wrapped_dek", "wrapped_dek_recovery")
    @classmethod
    def _check_wrapped(cls, v, info):
        return _bounded_b64(v, info.field_name, MIN_WRAPPED_BYTES, MAX_WRAPPED_BYTES)

    @field_validator("wrapped_dek_nonce", "wrapped_dek_recovery_nonce")
    @classmethod
    def _check_nonce(cls, v, info):
        return _bounded_b64(v, info.field_name, MIN_NONCE_BYTES, MAX_NONCE_BYTES)

    @field_validator("kdf_params")
    @classmethod
    def _check_params(cls, v):
        if len(v) > 12:
            raise ValueError("kdf_params must have at most 12 keys")
        return v

    def model_post_init(self, __context):
        # Reusing one salt for both wrappings would make the recovery path no
        # stronger than the password path against a precomputation attack.
        if self.password_salt == self.recovery_salt:
            raise ValueError("password_salt and recovery_salt must differ")


class JournalKeyBundleReWrap(JournalKeyBundleIn):
    """A standalone re-wrap (PUT /journal/key), e.g. after unlocking with the
    recovery code. Authenticated by the current account password so a stolen
    session alone cannot overwrite the bundle and orphan the journal."""

    current_password: str = Field(min_length=1)


class JournalKeyBundleOut(BaseModel):
    cipher: Cipher
    kdf: Kdf
    kdf_params: dict
    password_salt: str
    wrapped_dek: str
    wrapped_dek_nonce: str
    recovery_salt: str
    wrapped_dek_recovery: str
    wrapped_dek_recovery_nonce: str
    key_version: int
    # True → the account password changed without a re-wrap (any password reset
    # does this). The password path is dead; only the recovery code can unlock.
    password_wrap_stale: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JournalEntryCreate(BaseModel):
    entry_date: date
    ciphertext: str
    nonce: str
    key_version: int = Field(default=1, ge=1)
    # Tagging is never a gate (VISION §6): the blank page stays blank, and an
    # entry can be named later or never.
    emotions: list[EmotionIn] = Field(default_factory=list)

    _check_ciphertext = field_validator("ciphertext")(_validate_ciphertext)
    _check_nonce = field_validator("nonce")(_validate_nonce)


class JournalEntryUpdate(BaseModel):
    """Ciphertext and nonce travel together: re-encrypting produces a new nonce,
    and accepting one without the other would let a client build a nonce-reuse
    footgun through this API."""

    entry_date: date | None = None
    ciphertext: str | None = None
    nonce: str | None = None
    key_version: int | None = Field(default=None, ge=1)
    emotions: list[EmotionIn] | None = None

    _check_ciphertext = field_validator("ciphertext")(_validate_ciphertext)
    _check_nonce = field_validator("nonce")(_validate_nonce)

    def model_post_init(self, __context):
        if (self.ciphertext is None) != (self.nonce is None):
            raise ValueError("ciphertext and nonce must be updated together")


class JournalTagsUpdate(BaseModel):
    """Tags-only write — the batch-tag-later path ("five days unnamed — name
    them?"). Deliberately does not touch the ciphertext, so naming a feeling
    costs no decrypt/re-encrypt round trip."""

    emotions: list[EmotionIn]


class JournalEntryResponse(BaseModel):
    id: uuid.UUID
    entry_date: date
    ciphertext: str
    nonce: str
    key_version: int
    emotions: list[EmotionOut]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JournalEntryListResponse(BaseModel):
    entries: list[JournalEntryResponse]
    total: int
    next_cursor: str | None = None
    has_more: bool = False
