"""Web Push delivery (add-on to #6).

Self-hosted VAPID rather than FCM/OneSignal: a vendor would learn who is being
notified about what, on a product whose premise is a private mirror.

**What a push payload may contain.** Almost nothing. It is decrypted by the
browser and shown on a lock screen, so it must never carry the thing itself —
not a message body, not a book someone is reading, not a handle. It carries a
kind, a destination, and copy generic enough to be read by whoever picks the
phone up. The app is where the content lives; the push is only a knock.

**A push that arrives late still arrives.** The default TTL in pywebpush is 0,
which tells the push service "deliver this only if the device is connected right
now, otherwise throw it away". A phone that is asleep, out of signal, or simply
not currently holding a connection therefore got nothing at all — which is the
exact shape of "the app was closed so I never heard about it". PUSH_TTL asks the
service to hold it instead.

**Failures are not all equal.** 404/410 means the subscription is permanently
dead (browser uninstalled, permission revoked) and the row is deleted. Anything
else — a timeout, a 500 from the push service, a network blip — is transient and
the row is left alone. Deleting on a transient failure would silently unsubscribe
people whose push service had a bad afternoon.
"""

import asyncio
import logging
import uuid
from urllib.parse import quote

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
    "resonance_reach": ("Bibliome", "Someone reached out."),
    "resonance_connected": ("Bibliome", "Someone said yes."),
    "chat_reaction": ("Bibliome", "Someone reacted to what you wrote."),
    "chat_mention": ("Bibliome", "Someone mentioned you."),
    "collection_joined": ("Bibliome", "Someone joined a collection you're in."),
    "collection_deleted": ("Bibliome", "A collection you were in was closed."),
    "dna_shifted": ("Bibliome", "Your DNA moved."),
    # Sent only by POST /push/test, at the reader's own request.
    "push_test": ("Bibliome", "Notifications are working."),
    "weekly_digest": ("Bibliome", "Your reading week is ready."),
    # Tier 0. Named plainly on purpose: this is the one kind where a reader
    # needs to know what happened without unlocking anything, because the
    # answer may be "that was not me".
    "password_reset": ("Bibliome security", "Your password was reset."),
    "password_changed": ("Bibliome security", "Your password was changed."),
}
_DEFAULT_COPY = ("Bibliome", "Something happened in your library.")

# How long the push service may hold an undeliverable push, in seconds. A day:
# long enough to survive a night with the phone off, short enough that nobody is
# woken by something that stopped being true yesterday.
PUSH_TTL = 24 * 60 * 60


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
    # Where to go when tapped. Built from ids the client already has to be
    # authorised for — the push grants nothing by itself.
    url = _url_for(kind, payload)
    return {
        "title": title,
        "body": body,
        "url": url,
        # Collapses repeats on the device, the same way `batch_key` collapses
        # them in the notification list: five messages about one book are one
        # knock, not five.
        #
        # Keyed on the destination rather than a hand-picked id, so anything
        # that opens a different page gets its own notification. The old key
        # only knew about collections and threads, which quietly collapsed
        # every echo reply — two different echoes, one notification, opening
        # whichever arrived last.
        "tag": f"{kind}:{url}",
    }


def _url_for(kind: str, payload: dict) -> str:
    """Where a tapped push lands.

    This mirrors `notificationTarget()` in the frontend
    (src/components/notifications/target.js), which is what a click in the
    in-app notification centre uses. It has to be duplicated rather than shared:
    the push payload deliberately carries no content, only a destination, so the
    worker has nothing to compute a route from. Keep the two in step — every
    kind this does not know falls through to "/", and a notification that always
    opens the home page is indistinguishable from one that does nothing.
    """
    collection = payload.get("collection_id")
    discussion = f"/collections/{collection}/discussion" if collection else None

    # Tier 0. The only useful thing to do about a security notice is look at the
    # account, not the shelf.
    if kind in ("password_reset", "password_changed"):
        return "/settings?section=security"

    if kind == "echo_reply":
        echo_id = payload.get("echo_id")
        return f"/echoes?echo={quote(str(echo_id))}" if echo_id else "/echoes"

    if kind in ("resonance_reach", "resonance_connected", "resonance_message"):
        return "/resonance"

    if kind == "collection_message" and discussion:
        # Batched payloads can merge several books; the room is the fallback.
        return f"{discussion}/{payload['book_id']}" if payload.get("book_id") else discussion

    if kind == "chat_reaction":
        # One kind, both chat surfaces — the payload shape tells them apart.
        if payload.get("thread_id"):
            return "/resonance"
        return discussion or "/"

    if kind in ("chat_mention", "collection_joined"):
        return discussion or "/"

    if kind == "dna_shifted":
        return "/?view=dna"

    if kind == "push_test":
        return "/settings?section=notifications"

    # weekly_digest, collection_deleted (the room is gone) and anything new.
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
            ttl=PUSH_TTL,
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
