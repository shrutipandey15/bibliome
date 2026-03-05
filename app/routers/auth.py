import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import auth_limiter
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_refresh_token_str,
    register_user,
    save_refresh_token,
    validate_refresh_token,
    hash_password,
)
from app.services.email_service import send_reset_email, _generate_token

logger = logging.getLogger("bookdna.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

_failed_attempts: dict[str, list[float]] = defaultdict(list)
LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW = 900


def _check_lockout(email: str) -> None:
    """Check if account is locked due to too many failed login attempts."""
    now = time.monotonic()
    key = email.lower().strip()
    _failed_attempts[key] = [t for t in _failed_attempts[key] if now - t < LOCKOUT_WINDOW]
    if len(_failed_attempts[key]) >= LOCKOUT_THRESHOLD:
        remaining = int(LOCKOUT_WINDOW - (now - _failed_attempts[key][0]))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {remaining // 60} minutes.",
        )


def _record_failed(email: str) -> None:
    _failed_attempts[email.lower().strip()].append(time.monotonic())


def _clear_failed(email: str) -> None:
    _failed_attempts.pop(email.lower().strip(), None)


from app.middleware.rate_limit import RateLimiter
register_limiter = RateLimiter(max_requests=3, window_seconds=3600, prefix="register")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Register a new user."""
    auth_limiter.check(request)
    register_limiter.check(request)
    email = data.email.lower().strip()
    username = data.username.strip()

    disposable_domains = {"tempmail", "throwaway", "mailinator", "guerrilla", "yopmail", "10minutemail"}
    email_domain = email.split("@")[1].split(".")[0] if "@" in email else ""
    if email_domain in disposable_domains:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please use a real email address")

    try:
        user = await register_user(db, email=email, username=username, password=data.password, display_name=data.display_name)
        logger.info("New registration: %s (%s)", username, email)
        return user
    except ValueError as e:
        await asyncio.sleep(0.1)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Request a password reset email."""
    auth_limiter.check(request)

    email = data.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        await asyncio.sleep(0.3)
        return {"message": "If that email exists, a reset link has been sent"}

    token = _generate_token()
    user.reset_token = token
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await db.flush()
    await send_reset_email(email, user.username, token)

    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using the token from the reset email."""
    result = await db.execute(
        select(User).where(User.reset_token == data.token)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset token")

    if user.reset_token_expires and user.reset_token_expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")

    user.password_hash = await hash_password(data.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    await db.flush()

    logger.info("Password reset for %s", user.email)
    return {"message": "Password updated successfully"}


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate and return tokens."""
    auth_limiter.check(request)

    email = data.email.lower().strip()

    _check_lockout(email)

    user = await authenticate_user(db, email, data.password)
    if not user:
        _record_failed(email)
        failed_count = len(_failed_attempts.get(email, []))
        remaining = LOCKOUT_THRESHOLD - failed_count

        logger.warning("Failed login for %s (%d/%d attempts)", email, failed_count, LOCKOUT_THRESHOLD)

        await asyncio.sleep(0.3)

        detail = "Invalid email or password"
        if 0 < remaining <= 2:
            detail = f"Invalid email or password. {remaining} attempts remaining before lockout."

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    _clear_failed(email)

    access_token = create_access_token(user.id)
    refresh_token, expires_at = create_refresh_token_str(user.id)
    await save_refresh_token(db, user.id, refresh_token, expires_at)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Get new tokens using a refresh token (rotation: old token is revoked)."""
    user = await validate_refresh_token(db, data.refresh_token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    access_token = create_access_token(user.id)
    new_refresh, expires_at = create_refresh_token_str(user.id)
    await save_refresh_token(db, user.id, new_refresh, expires_at)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user