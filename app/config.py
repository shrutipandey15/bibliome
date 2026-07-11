from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bookdna"

    # JWT
    SECRET_KEY: str = "change-this-to-a-random-secret-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # 30 days per the locked auth-cookie contract (authCookieContract.md).
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Refresh-token cookie (httpOnly, SameSite=Strict, scoped to /api/auth).
    REFRESH_COOKIE_NAME: str = "bookdna_refresh"
    REFRESH_COOKIE_PATH: str = "/api/auth"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Redis (rate limiting — optional, falls back to in-memory)
    REDIS_URL: str | None = None

    # Google Books API key — raises the anonymous shared-quota ceiling that 429s
    # under real traffic (P4-5). Optional; search still works (rate-limited) without it.
    GOOGLE_BOOKS_API_KEY: str | None = None

    # Number of trusted reverse proxies in front of the app (e.g. 1 for a single
    # nginx). Controls how X-Forwarded-For is interpreted for rate limiting: only
    # this many rightmost hops are trusted, so a client cannot spoof its IP by
    # sending its own X-Forwarded-For (P1-5). 0 = ignore XFF, use the socket peer.
    TRUSTED_PROXY_COUNT: int = 0

    # App
    ENVIRONMENT: str = "development"
    APP_NAME: str = "Book DNA"
    API_V1_PREFIX: str = "/api"

    # Email (SMTP)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str = "noreply@bookdna.app"
    FRONTEND_URL: str = "http://localhost:3000"

    @property
    def email_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASSWORD)

    @property
    def cookie_secure(self) -> bool:
        # Secure flag in production; omitted on plain-http localhost dev.
        return self.ENVIRONMENT == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    # Security check
    @model_validator(mode='after')
    def check_production_security(self):
        if self.ENVIRONMENT == "production":
            key = self.SECRET_KEY.lower()
            # Catch both the code default ("change-this…") and the env.production
            # placeholder ("CHANGE_THIS_TO_RANDOM_64_CHARS"); the old check only
            # matched the former, so the placeholder shipped to prod unnoticed.
            if key.startswith("change-this") or key.startswith("change_this") or len(self.SECRET_KEY) < 32:
                raise ValueError("CRITICAL: You must set a secure SECRET_KEY env var in production!")
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()