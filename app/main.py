from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import auth, entries, dna, public, user

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: could initialize DB pool, run migrations, etc.
    print(f"Starting {settings.APP_NAME} API ({settings.ENVIRONMENT})")
    yield
    # Shutdown: cleanup
    print(f"Shutting down {settings.APP_NAME} API")


app = FastAPI(
    title=settings.APP_NAME,
    description="The emotional fingerprint of your reading life",
    version="0.1.0",
    lifespan=lifespan,
)

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


@app.get("/health")
async def health_check():
    return {"status": "alive", "app": settings.APP_NAME}