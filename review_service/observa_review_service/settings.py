"""Runtime configuration, read from the environment and review_service/.env."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite:///./data/review.sqlite3"
    MAX_REVIEW_CYCLES: int = Field(default=3, ge=1)
    OUTBOX_MAX_ATTEMPTS: int = Field(default=8, ge=1)
    INTERPRETER: str = Field(default="fake", pattern="^(fake|groq)$")
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    SERVICE_TOKEN_SHA256: str | None = None
    SESSION_SECRET: str | None = None
    REVIEWERS_FILE: str = "reviewers.json"
    OUTBOX_LEASE_SECONDS: int = Field(default=60, ge=1)
    MEMORY_SINK_DIR: str = "./data/memory"
