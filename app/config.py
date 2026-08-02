from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache

# The only accepted values for ENVIRONMENT. Anything else is a configuration
# error, not a hint — see check_production_security.
VALID_ENVIRONMENTS = ("development", "test", "production")


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bibliome"

    # JWT
    SECRET_KEY: str = "change-this-to-a-random-secret-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # 30 days per the locked auth-cookie contract (authCookieContract.md).
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Refresh-token cookie (httpOnly, SameSite=Strict, scoped to /api/auth).
    REFRESH_COOKIE_NAME: str = "bibliome_refresh"
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

    # Connection pool sizing. Deliberately small: each uvicorn worker gets its own
    # pool, so the Postgres connection ceiling is workers × (POOL_SIZE + MAX_OVERFLOW).
    # Raise via env once the box has room for it.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    # Echo every SQL statement to the log. Off by default even in development:
    # it drowns the request log and the app's own startup lines. Set SQL_ECHO=1
    # when actually debugging a query.
    SQL_ECHO: bool = False

    # Git SHA of the running build, stamped into .env at deploy time. Surfaced at
    # GET /api/meta/version so you can tell what is actually deployed.
    GIT_SHA: str = "unknown"

    # App
    ENVIRONMENT: str = "development"
    APP_NAME: str = "Bibliome"
    API_V1_PREFIX: str = "/api"

    # Book aggregate confidence tiers (B8.4). Tunable — these are honesty
    # thresholds, not performance knobs.
    #   < EMERGING            -> nothing real to say
    #   EMERGING .. CONFIRMED -> "early readings"
    #   >= CONFIRMED          -> enough readers to call it a pattern
    # CONFIRMED mirrors the 5-book DNA gate: the same bar for the same reason.
    AGGREGATE_EMERGING_MIN_READERS: int = 1
    AGGREGATE_CONFIRMED_MIN_READERS: int = 5

    # Below this many readers an aggregate is not served to *other* users: with
    # one or two readers the "aggregate" is effectively one person's private
    # tagging, and serving it would de-anonymize them (B8.6).
    AGGREGATE_PUBLIC_MIN_READERS: int = 3

    # A *separate* question from the privacy floor above: is this book's profile
    # stable enough to measure an individual reader's deviation against? That
    # needs more readers than mere anonymity does — below this, any one reader is
    # too large a fraction of the population to deviate from it meaningfully.
    # Tied to the `confirmed` tier for the same reason it exists.
    DEVIATION_MIN_READERS: int = 5

    # Email (SMTP)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str = "noreply@bibliome.app"
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
        # Normalise and reject anything not on the list. Previously any value
        # other than the exact string "production" — including a typo like
        # "Production" or "prod" — silently selected the development posture:
        # placeholder SECRET_KEY accepted, cookie_secure off, /docs open. A typo
        # should crash the process, not quietly downgrade it.
        env = self.ENVIRONMENT.strip().lower()
        if env not in VALID_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {', '.join(VALID_ENVIRONMENTS)} (got {self.ENVIRONMENT!r})"
            )
        self.ENVIRONMENT = env

        if env == "production":
            key = self.SECRET_KEY.lower()
            # Catch both the code default ("change-this…") and the env.production
            # placeholder ("CHANGE_THIS_TO_RANDOM_64_CHARS"); the old check only
            # matched the former, so the placeholder shipped to prod unnoticed.
            if key.startswith("change-this") or key.startswith("change_this") or len(self.SECRET_KEY) < 32:
                raise ValueError("CRITICAL: You must set a secure SECRET_KEY env var in production!")
            return self

        # Not production — but is this actually a production box that forgot to
        # say so? ENVIRONMENT defaults to "development", so an absent or dropped
        # env var is indistinguishable from a real dev machine, and that is the
        # bypass: the whole block above simply never runs. FRONTEND_URL is the
        # independent tell. A public https origin is not a laptop.
        frontend = self.FRONTEND_URL.strip().lower()
        if frontend.startswith("https://") and not any(
            host in frontend for host in ("localhost", "127.0.0.1", "0.0.0.0")
        ):
            raise ValueError(
                f"CRITICAL: ENVIRONMENT is {env!r} but FRONTEND_URL is {self.FRONTEND_URL!r}. "
                "This looks like a production deployment running with development "
                "security (insecure cookies, open /docs, unchecked SECRET_KEY). "
                "Set ENVIRONMENT=production."
            )
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()