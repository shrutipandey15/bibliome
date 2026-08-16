"""Web Push delivery (add-on to #6).

Self-hosted VAPID rather than FCM/OneSignal: a vendor would learn who is being
notified about what, on a product whose premise is a private mirror.

**What a push payload may contain.** Almost nothing. It is decrypted by the
browser and shown on a lock screen, so it must never carry the thing itself —
not a message body, not a book someone is reading, not a handle. It carries a
kind, a destination, and copy generic enough to be read by whoever picks the
phone up. The app is where the content lives; the push is only a knock.

**Failures are not all equal.** 404/410 means the subscription is permanently
dead (browser uninstalled, permission revoked) and the row is deleted. Anything
else — a timeout, a 500 from the push service, a network blip — is transient and
the row is left alone. Deleting on a transient failure would silently unsubscribe
people whose push service had a bad afternoon.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.push import PushSubscription

logger = logging.getLogger("bibliome.push")

# Copy shown on the lock screen, per notification kind. Deliberately vague: a
# push is read by whoever is holding the phone, which is not always its owner.
PUSH_COPY: dict[str, tuple[str, str]] = {
    "collection_message": ("Bibliome", "Someone wrote in a collection you're in."),
    "resonance_message": ("Bibliome", "You have a new message."),
    "echo_reply": ("Bibliome", "Someone replied to your echo."),
    "resonance_match": ("Bibliome", "Someone felt what you felt."),
    "dna_shifted": ("Bibliome", "Your DNA moved."),
}
_DEFAULT_COPY = ("Bibliome", "Something happened in your library.")


async def save_subscription(
    db: AsyncSession, user_id: uuid.UUID, endpoint: str, p256dh: str, auth: str
) -> None:
    """Upsert by endpoint.

    The endpoint IS the device. Re-subscribing in the same browser returns the
    same endpoint, so inserting blindly would pile up rows that all ring the same
    phone. The upsert also re-points an endpoint at the current user, which is
    what should happen when two people share a device.
    """
    stmt = pg_insert(PushSubscription).values(
        user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth,
    ).on_conflict_do_update(
        index_elements=[PushSubscription.endpoint],
        set_={"user_id": user_id, "p256dh": p256dh, "auth": auth},
    )
    await db.execute(stmt)
    await db.flush()


async def delete_subscription(db: AsyncSession, endpoint: str) -> None:
    sub = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    )).scalar_one_or_none()
    if sub is not None:
        await db.delete(sub)
        await db.flush()


def _payload(kind: str, payload: dict) -> dict:
    title, body = PUSH_COPY.get(kind, _DEFAULT_COPY)
    return {
        "title": title,
        "body": body,
        # Where to go when tapped. Built from ids the client already has to be
        # authorised for — the push grants nothing by itself.
        "url": _url_for(kind, payload),
        # Collapses repeats on the device, the same way `batch_key` collapses
        # them in the notification list: five messages about one book are one
        # knock, not five.
        "tag": f"{kind}:{payload.get('collection_id') or payload.get('thread_id') or ''}",
    }


def _url_for(kind: str, payload: dict) -> str:
    if kind == "collection_message" and payload.get("collection_id"):
        base = f"/collections/{payload['collection_id']}/discussion"
        return f"{base}/{payload['book_id']}" if payload.get("book_id") else base
    if kind == "resonance_message":
        return "/resonance"
    if kind == "echo_reply":
        return "/echoes"
    return "/"


def _send_one_sync(sub_row: dict, data: dict) -> int | None:
    """Blocking send. Returns an HTTP status to act on, or None if it worked.

    pywebpush is synchronous and does its own TLS, so it runs in a thread rather
    than blocking the event loop for every subscriber in a collection.
    """
    from pywebpush import WebPushException, webpush

    settings = get_settings()
    try:
        webpush(
            subscription_info={
                "endpoint": sub_row["endpoint"],
                "keys": {"p256dh": sub_row["p256dh"], "auth": sub_row["auth"]},
            },
            data=data,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            timeout=10,
        )
        return None
    except WebPushException as e:
        return getattr(e.response, "status_code", None) or 0
    except Exception:  # network, DNS, TLS — transient by assumption
        return 0


async def push_to_user(
    db: AsyncSession, user_id: uuid.UUID, kind: str, payload: dict
) -> int:
    """Ring every device this reader has allowed. Returns how many were sent.

    Never raises: a push is a courtesy on top of a notification that has already
    been recorded. If the push service is down, the notification is still in the
    app, and failing the request that caused it would be the wrong trade.
    """
    settings = get_settings()
    if not settings.push_enabled:
        return 0

    subs = (await db.execute(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    )).scalars().all()
    if not subs:
        return 0

    import json
    data = json.dumps(_payload(kind, payload))
    rows = [{"endpoint": s.endpoint, "p256dh": s.p256dh, "auth": s.auth} for s in subs]

    results = await asyncio.gather(
        *(asyncio.to_thread(_send_one_sync, r, data) for r in rows),
        return_exceptions=True,
    )

    sent, dead = 0, []
    for row, status in zip(rows, results):
        if isinstance(status, BaseException):
            continue
        if status is None:
            sent += 1
        elif status in (404, 410):
            # Permanently gone. Anything else is transient and left alone.
            dead.append(row["endpoint"])
        else:
            logger.warning("push failed status=%s", status)

    for endpoint in dead:
        await delete_subscription(db, endpoint)

    return sent
