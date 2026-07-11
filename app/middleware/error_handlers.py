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
import traceback
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("bookdna")


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
        elif "users_email_key" in err_str or "ix_users_email" in err_str:
            detail = "This email is already registered"
        elif "users_username_key" in err_str or "ix_users_username" in err_str:
            detail = "This username is already taken"

        logger.warning(
            "Integrity error on %s %s: %s",
            request.method, request.url.path, err_str,
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
            str(exc),
            traceback.format_exc(),
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
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if environment == "development" else logging.WARNING
    )