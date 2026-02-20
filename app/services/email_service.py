"""
Email Service

Sends verification and password reset emails via SMTP.
Works with Gmail, SendGrid, Mailgun, or any SMTP provider.

If SMTP is not configured, emails are logged to console (dev mode).
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import get_settings

logger = logging.getLogger("bookdna.email")
settings = get_settings()


def _generate_token() -> str:
    """Generate a URL-safe verification/reset token."""
    return secrets.token_urlsafe(32)


def _build_verification_email(username: str, token: str) -> tuple[str, str]:
    """Build email verification subject and HTML body."""
    link = f"{settings.FRONTEND_URL}/verify?token={token}"
    subject = "Verify your Book DNA account"
    body = f"""
    <div style="font-family: Georgia, serif; max-width: 500px; margin: 0 auto; color: #333;">
      <h2 style="color: #1a1a1e;">Welcome to Book DNA, {username}</h2>
      <p>Every reader leaves emotional fingerprints. Yours are waiting to be mapped.</p>
      <p>Click below to verify your email and unlock your reading personality:</p>
      <p style="text-align: center; margin: 30px 0;">
        <a href="{link}" style="background: #B8964E; color: white; padding: 12px 32px;
           border-radius: 6px; text-decoration: none; font-weight: bold;">
          Verify My Email
        </a>
      </p>
      <p style="color: #888; font-size: 12px;">
        If you didn't create this account, ignore this email.<br>
        This link expires in 24 hours.
      </p>
      <p style="color: #aaa; font-size: 11px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 15px;">
        Book DNA — because the books that change you deserve more than a star rating.
      </p>
    </div>
    """
    return subject, body


def _build_reset_email(username: str, token: str) -> tuple[str, str]:
    """Build password reset subject and HTML body."""
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    subject = "Reset your Book DNA password"
    body = f"""
    <div style="font-family: Georgia, serif; max-width: 500px; margin: 0 auto; color: #333;">
      <h2 style="color: #1a1a1e;">Password Reset</h2>
      <p>Hi {username}, we received a request to reset your password.</p>
      <p style="text-align: center; margin: 30px 0;">
        <a href="{link}" style="background: #B8964E; color: white; padding: 12px 32px;
           border-radius: 6px; text-decoration: none; font-weight: bold;">
          Reset Password
        </a>
      </p>
      <p style="color: #888; font-size: 12px;">
        If you didn't request this, you can safely ignore this email.<br>
        This link expires in 1 hour.
      </p>
    </div>
    """
    return subject, body


async def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """
    Send an email via SMTP.
    Returns True if sent, False if failed.
    Falls back to console logging if SMTP not configured.
    """
    if not settings.email_enabled:
        logger.info("EMAIL (dev mode — SMTP not configured):\n  To: %s\n  Subject: %s\n  Body: [HTML email]", to_email, subject)
        return True

    try:
        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            use_tls=True,
        )
        logger.info("Email sent to %s: %s", to_email, subject)
        return True
    except ImportError:
        logger.warning("aiosmtplib not installed — email logged to console instead")
        logger.info("EMAIL:\n  To: %s\n  Subject: %s", to_email, subject)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False


async def send_verification_email(to_email: str, username: str, token: str) -> bool:
    """Send email verification link."""
    subject, body = _build_verification_email(username, token)
    return await send_email(to_email, subject, body)


async def send_reset_email(to_email: str, username: str, token: str) -> bool:
    """Send password reset link."""
    subject, body = _build_reset_email(username, token)
    return await send_email(to_email, subject, body)