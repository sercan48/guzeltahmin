import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    BOT_TOKEN: str
    API_SERVICE_URL: str = "http://localhost:8000"
    INTERNAL_API_TOKEN: str = "secret-token"
    HMAC_SECRET: str = "hmac-shared-secret-change-in-production"
    VIP_CHANNEL_ID: str

    model_config = SettingsConfigDict(
        env_file=[".env", "../.env", "../../.env", "../../../.env"],
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = BotSettings()
