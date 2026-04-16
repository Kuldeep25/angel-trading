from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Angel One
    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Twilio WhatsApp
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_whatsapp_to: str = ""

    # App
    default_capital: float = 100000.0
    app_port: int = 8000

    # Derived paths (set at runtime)
    strategies_dir: str = "strategy/strategies"
    strategies_meta_file: str = "strategy/strategies_meta.json"
    instruments_cache_file: str = "angel/instruments_cache.json"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
