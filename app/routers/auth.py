import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import auth_limiter, get_client_ip, login_lockout, RateLimiter
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    AuthResponse,
    AuthUser,
    LoginRequest,
    RegisterRequest,
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
    revoke_refresh_token,
    save_refresh_token,
    validate_refresh_token,
    hash_password,
)
from app.services.email_service import send_reset_email, _generate_token
from app.services.journal_service import mark_password_wrap_stale
from app.utils.cookies import set_refresh_cookie, clear_refresh_cookie
from app.utils.redact import redact_email
from app.models.notification import TIER_SECURITY
from app.services.notification_service import notify


def _access_expiry_seconds() -> int:
    return get_settings().ACCESS_TOKEN_EXPIRE_MINUTES * 60


def _auth_user(user: User) -> AuthUser:
    # `handle` is the username today; Phase 3 (B3.1) introduces real handles.
    return AuthUser(id=user.id, handle=user.username, email=user.email)


async def _start_session(db: AsyncSession, user: User, response: Response) -> str:
    """Issue an access token and set a fresh refresh-token cookie. Returns the access token."""
    access_token = create_access_token(user.id)
    refresh_token, expires_at = create_refresh_token_str(user.id)
    await save_refresh_token(db, user.id, refresh_token, expires_at)
    set_refresh_cookie(response, refresh_token)
    return access_token

logger = logging.getLogger("bibliome.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

register_limiter = RateLimiter(max_requests=3, window_seconds=3600, prefix="register")


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user and auto-login (access token + refresh cookie)."""
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
    except ValueError:
        await asyncio.sleep(0.1)
        # Generic message: distinct "email taken" vs "username taken" replies were
        # an account-enumeration oracle (P1-3).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email or username is not available.",
        )

    logger.info("New registration: %s (%s)", username, redact_email(email))
    access_token = await _start_session(db, user, response)
    return AuthResponse(access_token=access_token, expires_in=_access_expiry_seconds(), user=_auth_user(user))


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
async def reset_password(data: ResetPasswordRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Reset password using the token from the reset email.

    A reset proves control of the email, not knowledge of the old password — so
    the journal's password-wrapped data key becomes permanently unusable here.
    Nobody, including us, can re-wrap it: the server has never held that key.

    We therefore mark the wrap stale and say so in the response. With the recovery
    code the journal comes back; without it, it is gone for good. The API states
    that plainly at the moment it becomes true (journalCryptoContract.md §5).
    """
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
    # The password wrap of the journal key is now dead. The recovery wrap is not —
    # it never depended on the password — so the bundle stays exactly where it is.
    had_journal = await mark_password_wrap_stale(db, user.id)
    # Recovering the account must log out any existing (possibly attacker) sessions.
    await revoke_all_refresh_tokens(db, user.id)
    await db.flush()
    clear_refresh_cookie(response)

    await notify(db, user.id, TIER_SECURITY, "password_reset",
                 payload={"message": "Your password was reset."})

    logger.info("Password reset for user %s (%s)", user.id, redact_email(user.email))
    body: dict = {"message": "Password updated successfully"}
    if had_journal:
        body["journal"] = {
            "locked": True,
            "recoverable_with_recovery_code": True,
            "message": (
                "Your journal was encrypted with your old password, and we never had "
                "the key. Only your recovery code can unlock it now — without that "
                "code those entries are permanently unreadable, by you and by us."
            ),
        }
    return body


@router.post("/login", response_model=AuthResponse)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate; return an access token and set the refresh-token cookie."""
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
        logger.warning("Failed login for %s", redact_email(email))
        await asyncio.sleep(0.3)
        # Generic — no "N attempts remaining", which confirmed the account existed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    await login_lockout.clear(lockout_key)

    access_token = await _start_session(db, user, response)
    return AuthResponse(access_token=access_token, expires_in=_access_expiry_seconds(), user=_auth_user(user))


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Rotate the refresh cookie and return a new access token.

    The credential is the httpOnly cookie — there is no request body. On any
    failure the cookie is cleared so the client falls back to login cleanly.
    """
    token = request.cookies.get(get_settings().REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await validate_refresh_token(db, token)  # rotation: revokes the used token
    if not user:
        # Returned, not raised. Raising HTTPException makes FastAPI build a fresh
        # response and drop everything set on the injected one — including the
        # cookie deletion, which left a dead cookie in the browser that re-failed
        # every subsequent refresh.
        failed = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid or expired refresh token"},
        )
        clear_refresh_cookie(failed)
        return failed

    access_token = await _start_session(db, user, response)
    return AccessTokenResponse(access_token=access_token, expires_in=_access_expiry_seconds())


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Revoke the current refresh token and clear the cookie.

    Uses the cookie as the credential (no valid access token required), so a
    client with an expired access token can still cleanly end its session.
    """
    token = request.cookies.get(get_settings().REFRESH_COOKIE_NAME)
    if token:
        await revoke_refresh_token(db, token)
    clear_refresh_cookie(response)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user