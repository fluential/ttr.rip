from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import RedisDsn

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./test.db"
    REDIS_URL: RedisDsn = "redis://localhost:6379/0"
    SECRET_KEY: str = "a_very_secret_key"
    ENCRYPTION_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    DEBUG_MODE: bool = False
    SCHEDULER_INTERVAL_SECONDS: int = 5
    STATS_CACHE_TTL_SECONDS: int = 10

    # For "Login with Telegram" feature
    TELEGRAM_BOT_NAME: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_AUTH_ENABLED: bool = False
    
    # Auto-cleanup settings
    CLEANUP_ENABLED: bool = False
    CLEANUP_INACTIVE_DAYS: int = 180
    CLEANUP_INTERVAL_HOURS: int = 24
    
    # Auto-start Celery worker
    AUTO_START_WORKER: bool = True
    WORKER_CONCURRENCY: int = 2

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
