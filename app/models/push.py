"""Web Push subscriptions (add-on to #6).

One row per browser-and-device a reader has allowed notifications on — not one
per reader. The same person on a phone and a laptop is two subscriptions, and
both should ring.

The ``endpoint`` is the push service's URL for that device, and it is the
identity here: it is unique, and re-subscribing in the same browser returns the
same value. An upsert on it is what stops a reinstall accumulating dead rows.

``p256dh`` and ``auth`` are the browser's key material for payload encryption.
They are useless without the endpoint, and the endpoint is useless without our
VAPID private key — but they are stored as given and never logged.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Unique: this IS the device. Re-subscribing upserts rather than duplicating.
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    # For pruning: a subscription that has failed permanently is deleted, but a
    # long-quiet one is left alone — silence is not death.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
