from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./test.db"
    SECRET_KEY: str = "a_very_secret_key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    DEBUG_MODE: bool = False

    # For "Login with Telegram" feature
    TELEGRAM_BOT_NAME: str = ""
    TELEGRAM_BOT_TOKEN: str = ""

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
