import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    DATABASE_URL: str
    REDIS_URL: Optional[str] = None
    VIP_CHANNEL_ID: str
    INTERNAL_API_TOKEN: str = "secret-token"
    HMAC_SECRET: str = "hmac-shared-secret-change-in-production"
    JWT_SECRET: str = "jwt-shared-secret-change-in-production"

    ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000

    # Pydantic v2 configuration settings
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env", "../../.env", "../../../.env"],
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

