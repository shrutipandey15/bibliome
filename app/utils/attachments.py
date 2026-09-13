"""Chat image attachments (Resonance letters + collection rooms).

Stored on local disk (there is no S3/object storage configured for this
deployment — see CHAT_UPLOAD_DIR in app/config.py) under UUID filenames, never
the client's own filename: a name like "../../etc/passwd.png" must never reach
`os.path.join`, and a UUID sidesteps the whole class of path-traversal bug by
construction rather than by sanitizing input.

The format is SNIFFED from the file's own bytes, not trusted from the client's
Content-Type header or filename extension — both are attacker-controlled and
prove nothing about what's actually in the upload. Limited to the three
formats a phone camera or a screenshot actually produces; anything else (SVG,
which can carry script; GIF; HEIC) is refused rather than guessed at or
transcoded.
"""

import os
import uuid

from fastapi import HTTPException, UploadFile, status

from app.config import get_settings

_SIGNATURES: dict[bytes, tuple[str, str]] = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
}


def _sniff(raw: bytes) -> tuple[str, str] | None:
    for sig, kind in _SIGNATURES.items():
        if raw.startswith(sig):
            return kind
    # RIFF....WEBP — the four-byte format tag sits at offset 8, after the
    # 4-byte "RIFF" tag and a 4-byte little-endian chunk size.
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


async def save_chat_image(file: UploadFile) -> tuple[str, str]:
    """Validate and persist an uploaded chat image.

    Returns (relative_path, content_type) to store on the message row.
    Raises HTTPException(400/413) for anything that isn't a real, small-enough
    image — refused outright, the same stance the message pipeline takes on a
    threat rather than holding it silently (see resonance_service.post_message).
    """
    settings = get_settings()
    raw = await file.read()
    if len(raw) > settings.CHAT_MAX_ATTACHMENT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "That image is too large")
    sniffed = _sniff(raw)
    if sniffed is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PNG, JPEG or WEBP images are supported")
    ext, content_type = sniffed

    os.makedirs(settings.CHAT_UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4()}.{ext}"
    path = os.path.join(settings.CHAT_UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(raw)
    return path, content_type


def delete_chat_attachment(path: str | None) -> None:
    """Best-effort cleanup on message delete. Never raises — a missing or
    already-removed file must not turn "delete my message" into a 500."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
