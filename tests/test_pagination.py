"""Keyset cursor codec tests (B1.4).

The DB-level tie-break behaviour is covered by the API smoke tests; here we lock
the cursor encoding/decoding contract and the reject-malformed-cursor behaviour.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.entry_service import (
    InvalidCursor,
    _decode_cursor,
    _encode_cursor,
)


def test_cursor_roundtrip():
    ts = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    eid = uuid.uuid4()
    entry = SimpleNamespace(created_at=ts, id=eid)
    cursor = _encode_cursor(entry)
    decoded_ts, decoded_id = _decode_cursor(cursor)
    assert decoded_ts == ts
    assert decoded_id == eid


def test_cursor_encodes_both_fields():
    # Two entries with the SAME timestamp must produce DIFFERENT cursors,
    # otherwise same-timestamp rows get skipped/duplicated across pages.
    ts = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    a = SimpleNamespace(created_at=ts, id=uuid.uuid4())
    b = SimpleNamespace(created_at=ts, id=uuid.uuid4())
    assert _encode_cursor(a) != _encode_cursor(b)


@pytest.mark.parametrize("bad", ["", "not-a-cursor", "2026-02-03T00:00:00|not-a-uuid", "|"])
def test_malformed_cursor_raises(bad):
    with pytest.raises(InvalidCursor):
        _decode_cursor(bad)
