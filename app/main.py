import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.middleware.error_handlers import register_error_handlers, setup_logging
from app.routers import auth, entries, dna, public, user, books, admin, mirror, meta, echo, social, notifications, profile

settings = get_settings()
setup_logging(settings.ENVIRONMENT)
logger = logging.getLogger("bookdna.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    from app.database import engine

    # Verify DB is reachable before accepting traffic
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Starting %s API (%s) — DB connection verified", settings.APP_NAME, settings.ENVIRONMENT)

    yield

    # Release DB pool cleanly on shutdown
    await engine.dispose()
    logger.info("Shutting down %s API — DB pool closed", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    description="The emotional fingerprint of your reading life",
    version="0.1.0",
    lifespan=lifespan,
)

register_error_handlers(app)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(entries.router, prefix=settings.API_V1_PREFIX)
app.include_router(dna.router, prefix=settings.API_V1_PREFIX)
app.include_router(public.router, prefix=settings.API_V1_PREFIX)
app.include_router(user.router, prefix=settings.API_V1_PREFIX)
app.include_router(books.router, prefix=settings.API_V1_PREFIX)
app.include_router(admin.router, prefix=settings.API_V1_PREFIX)
app.include_router(mirror.router, prefix=settings.API_V1_PREFIX)
app.include_router(meta.router, prefix=settings.API_V1_PREFIX)
app.include_router(echo.router, prefix=settings.API_V1_PREFIX)
app.include_router(social.router, prefix=settings.API_V1_PREFIX)
app.include_router(notifications.router, prefix=settings.API_V1_PREFIX)
app.include_router(profile.router, prefix=settings.API_V1_PREFIX)


@app.get("/health")
async def health_check():
    return {"status": "alive", "app": settings.APP_NAME}