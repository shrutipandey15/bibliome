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
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Redis (rate limiting — optional, falls back to in-memory)
    REDIS_URL: str | None = None

    # App
    ENVIRONMENT: str = "development"
    APP_NAME: str = "Book DNA"
    API_V1_PREFIX: str = "/api"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    # Security check
    @model_validator(mode='after')
    def check_production_security(self):
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY.startswith("change-this"):
                raise ValueError("CRITICAL: You must set a secure SECRET_KEY env var in production!")
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()