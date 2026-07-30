"""
Global error handling for Book DNA API.

Catches all exceptions and returns a consistent JSON error format:
{
    "error": "human_readable_code",
    "detail": "What went wrong",
    "status": 400
}

Logs errors with request context for debugging.
"""

import logging
import re
import traceback
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.config import get_settings

logger = logging.getLogger("bookdna")

# SQLAlchemy stringifies a failed statement as
# "... [SQL: INSERT ...] [parameters: (...)] (Background on this error at: ...)".
# The SQL is useful in a log; the bound parameters are the user's data — entry
# notes, quotes, emails, and journal ciphertext.
_SQL_PARAMS_RE = re.compile(r"\[parameters:.*?\](?=\s*(?:\(Background|\[SQL|$))", re.DOTALL)


def redact_sql_parameters(text: str) -> str:
    """Strip bound parameters out of anything we're about to log.

    The journal makes this load-bearing rather than merely tidy: journal
    ciphertext must never reach the logs (journalCryptoContract.md §3), and an
    unhandled DB error on a journal write would otherwise stringify the whole blob
    straight into them. Nothing downstream needs the values to diagnose a failure.
    """
    if "[parameters:" not in text:
        return text
    redacted = _SQL_PARAMS_RE.sub("[parameters: REDACTED]", text)
    if "[parameters:" in redacted.replace("[parameters: REDACTED]", ""):
        # Unrecognized layout — truncate rather than risk logging the values.
        head, _, _ = redacted.partition("[parameters:")
        return head + "[parameters: REDACTED]"
    return redacted


def error_response(status_code: int, error: str, detail: str, error_id: str | None = None) -> JSONResponse:
    """Build a consistent error JSON response.

    Deliberately sets no CORS headers (P1-2): the previous ``ACAO: *`` +
    ``Allow-Credentials: true`` pair is spec-invalid and let any origin read
    error bodies (e.g. the 409 "email already registered" enumeration oracle),
    bypassing the CORSMiddleware allowlist. CORS on error responses is left to
    CORSMiddleware, which reflects only allowlisted origins.
    """
    body = {
        "error": error,
        "detail": detail,
        "status": status_code,
    }
    if error_id:
        body["error_id"] = error_id
    return JSONResponse(
        status_code=status_code,
        content=body,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the app."""

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """Pydantic / query param validation errors → 422 with readable messages."""
        errors = exc.errors()
        messages = []
        for err in errors:
            loc = " → ".join(str(l) for l in err.get("loc", []) if l != "body")
            msg = err.get("msg", "Invalid value")
            messages.append(f"{loc}: {msg}" if loc else msg)

        detail = "; ".join(messages) if messages else "Validation failed"

        logger.warning(
            "Validation error on %s %s: %s",
            request.method, request.url.path, detail,
        )

        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error="validation_error",
            detail=detail,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        """DB constraint violations → 409 with helpful message."""
        detail = "A record with this data already exists"
        err_str = str(exc.orig) if exc.orig else str(exc)

        if "uq_entry_emotion" in err_str:
            detail = "Duplicate emotion on this entry"
        elif any(k in err_str for k in ("users_email_key", "ix_users_email", "users_username_key", "ix_users_username")):
            # Generic: distinct email vs username messages were an enumeration oracle (P1-3).
            detail = "That email or username is not available."

        logger.warning(
            "Integrity error on %s %s: %s",
            request.method, request.url.path, redact_sql_parameters(err_str),
        )

        return error_response(
            status_code=status.HTTP_409_CONFLICT,
            error="conflict",
            detail=detail,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """
        Catch-all for unhandled exceptions → 500.
        Generates an error_id so the user can report it and we can find it in logs.
        Never leaks stack traces to the client.
        """
        error_id = uuid.uuid4().hex[:12]

        logger.error(
            "Unhandled exception [%s] on %s %s: %s\n%s",
            error_id,
            request.method,
            request.url.path,
            redact_sql_parameters(str(exc)),
            # The traceback carries the same stringified DB error, so it needs the
            # same redaction — otherwise the tail of the trace re-leaks the values.
            redact_sql_parameters(traceback.format_exc()),
        )

        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error="internal_error",
            detail="Something went wrong. If this persists, please report it.",
            error_id=error_id,
        )


def setup_logging(environment: str = "development") -> None:
    """Configure logging format based on environment."""
    level = logging.DEBUG if environment == "development" else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # SQL statement logging is opt-in via SQL_ECHO, not implied by development:
    # at INFO the engine logs every statement and buries the request log.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if get_settings().SQL_ECHO else logging.WARNING
    )