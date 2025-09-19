from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import RedisDsn
from typing import Optional

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
    INCR_BUFFER_ENABLED: bool = True
    INCR_BUFFER_FLUSH_INTERVAL_MS: int = 500
    INCR_BUFFER_MAX_OPS: int = 200

    # For "Login with Telegram" feature
    TELEGRAM_BOT_NAME: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_AUTH_ENABLED: bool = False
    
    # Auto-cleanup settings
    CLEANUP_ENABLED: bool = False
    CLEANUP_INACTIVE_DAYS: int = 180
    CLEANUP_INTERVAL_HOURS: int = 24
    
    # Auto-start Celery worker
    AUTO_START_EMBEDDED_WORKER: bool = False
    WORKER_CONCURRENCY: int = 50

    # GeoIP and Ping Logging
    SAVE_CHECK_LAST_LOGS: bool = True
    GEOIP_DATABASE_PATH: Optional[str] = None

    # User-specific slugs for URLs
    USER_SLUG_ENABLED: bool = True

    # Admin auth model (SPA): refresh token lifetime (days)
    ADMIN_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # X-Auth-Key hardening
    XAUTH_ENFORCE_ORIGIN: bool = False
    XAUTH_ENFORCE_IP: bool = False
    USER_ALLOWED_REQUEST_ORIGINS: list[str] = []

    # Security headers
    SECURITY_HEADERS_ENABLED: bool = True
    CSP_USER_DASHBOARD: str = "default-src 'self'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; font-src 'self' https: data:; img-src 'self' data: https:; frame-src 'self' https:; connect-src 'self' https:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
