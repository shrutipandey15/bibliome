import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.refresh_token import RefreshToken
from app.models.user import User

settings = get_settings()


async def hash_password(password: str) -> str:
    def _hash():
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return await asyncio.to_thread(_hash)


async def verify_password(plain: str, hashed: str) -> bool:
    def _verify():
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    return await asyncio.to_thread(_verify)


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token_str(user_id: uuid.UUID) -> tuple[str, datetime]:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, expire


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None.

    `algorithms` is pinned to our single symmetric algorithm, which is also what
    closes the alg-confusion class of bug that made python-jose's CVE dangerous.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.InvalidTokenError:
        return None


async def register_user(
    db: AsyncSession, email: str, username: str, password: str, display_name: str | None = None
) -> User:
    """Create a new user. Raises ValueError if email/username already exists."""
    # Check existing email
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise ValueError("Email already registered")

    # Check existing username
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise ValueError("Username already taken")

    hashed_pw = await hash_password(password)
    user = User(
        email=email,
        username=username,
        handle=username,  # public handle defaults to the username (Phase 3)
        password_hash=hashed_pw,
        display_name=display_name or username,
    )
    db.add(user)
    await db.flush()
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Verify credentials. Returns User or None."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not await verify_password(password, user.password_hash):
        return None
    return user


def hash_token(token: str) -> str:
    """Hash a high-entropy token (refresh/reset) for storage at rest.

    A plain SHA-256 is appropriate here (unlike passwords): these tokens carry
    full random entropy, so there's nothing to brute-force. Storing the hash
    means a DB leak no longer yields usable credentials (P1-1).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def save_refresh_token(db: AsyncSession, user_id: uuid.UUID, token: str, expires_at: datetime) -> None:
    """Store the *hash* of a refresh token in the database."""
    rt = RefreshToken(user_id=user_id, token=hash_token(token), expires_at=expires_at)
    db.add(rt)
    await db.flush()


async def validate_refresh_token(db: AsyncSession, token: str) -> User | None:
    """Validate a refresh token and return the associated user."""
    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        return None

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token == hash_token(token),
            RefreshToken.is_revoked == False,
        )
    )
    rt = result.scalar_one_or_none()
    if not rt:
        return None

    if rt.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None

    # Revoke the used refresh token (rotation)
    rt.is_revoked = True

    # Get user
    result = await db.execute(select(User).where(User.id == rt.user_id))
    return result.scalar_one_or_none()


async def revoke_refresh_token(db: AsyncSession, token: str) -> None:
    """Revoke a single refresh token by its value (used on logout)."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token == hash_token(token))
        .values(is_revoked=True)
    )


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every active refresh token for a user.

    Called on password change/reset so a stolen-account recovery actually logs
    the attacker out instead of leaving their session valid for up to 7 days.
    """
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.is_revoked == False)
        .values(is_revoked=True)
    )


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
