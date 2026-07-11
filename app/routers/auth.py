import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import auth_limiter, get_client_ip, login_lockout, RateLimiter
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
    hash_token,
    register_user,
    revoke_all_refresh_tokens,
    save_refresh_token,
    validate_refresh_token,
    hash_password,
)
from app.services.email_service import send_reset_email, _generate_token

logger = logging.getLogger("bookdna.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

register_limiter = RateLimiter(max_requests=3, window_seconds=3600, prefix="register")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Register a new user."""
    await auth_limiter.check(request)
    await register_limiter.check(request)
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
    except ValueError:
        await asyncio.sleep(0.1)
        # Generic message: distinct "email taken" vs "username taken" replies were
        # an account-enumeration oracle (P1-3).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email or username is not available.",
        )


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email.

    Returns the same response and takes ~the same time whether or not the email
    exists (P1-3): the SMTP round-trip — the dominant timing signal — is deferred
    to a post-response background task, and the former asymmetric sleep is gone.
    """
    await auth_limiter.check(request)

    email = data.email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        token = _generate_token()
        # Store only the hash; the plaintext token lives only in the emailed link (P1-1).
        user.reset_token = hash_token(token)
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.flush()
        background_tasks.add_task(send_reset_email, email, user.username, token)

    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using the token from the reset email."""
    result = await db.execute(
        select(User).where(User.reset_token == hash_token(data.token))
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset token")

    if user.reset_token_expires and user.reset_token_expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")

    user.password_hash = await hash_password(data.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    # Recovering the account must log out any existing (possibly attacker) sessions.
    await revoke_all_refresh_tokens(db, user.id)
    await db.flush()

    logger.info("Password reset for %s", user.email)
    return {"message": "Password updated successfully"}


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate and return tokens."""
    await auth_limiter.check(request)

    email = data.email.lower().strip()

    # Key lockout on (email, client IP), not email alone: a third party failing
    # logins against a victim's email must not be able to lock the victim out of
    # their own account (auth-DoS, P1-3). The victim comes from a different IP and
    # keeps a clean counter; genuine single-source brute force still trips it.
    lockout_key = f"{email}:{get_client_ip(request)}"
    await login_lockout.check_locked(lockout_key)

    user = await authenticate_user(db, email, data.password)
    if not user:
        await login_lockout.record(lockout_key)
        logger.warning("Failed login for %s", email)
        await asyncio.sleep(0.3)
        # Generic — no "N attempts remaining", which confirmed the account existed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    await login_lockout.clear(lockout_key)

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