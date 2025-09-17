from pydantic import BaseModel
from typing import Optional, Union, Any
from datetime import datetime
import uuid

# Token Schemas
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# User Schemas
class UserBase(BaseModel):
    username: Optional[str] = None

class UserCreate(UserBase):
    password: Optional[str] = None
    is_admin: bool = False
    auth_key: Optional[str] = None
    telegram_user_id: Optional[int] = None
    telegram_first_name: Optional[str] = None
    telegram_username: Optional[str] = None

class User(UserBase):
    id: int
    is_admin: bool
    auth_key: Optional[str] = None
    telegram_user_id: Optional[int] = None
    telegram_first_name: Optional[str] = None
    telegram_username: Optional[str] = None

    class Config:
        from_attributes = True

class UserKeyResponse(BaseModel):
    auth_key: str

# Check Schemas
class CheckBase(BaseModel):
    name: str
    slug: Optional[str] = None
    interval_seconds: int
    grace_seconds: int
    max_runtime_seconds: Optional[int] = None
    expected_content: Optional[str] = None
    expected_content_type: Optional[str] = None # 'present' or 'absent'
    use_regex_for_content: bool = False

class CheckCreate(CheckBase):
    pass

class CheckUpdate(CheckBase):
    pass


class CheckExport(BaseModel):
    name: str
    slug: Optional[str] = None
    interval_seconds: int
    grace_seconds: int
    expected_content: Optional[str] = None
    expected_content_type: Optional[str] = None
    use_regex_for_content: bool
    telegram_enabled: bool
    telegram_chat_id: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    slack_enabled: bool
    slack_webhook_url: Optional[str] = None
    discord_enabled: bool
    discord_webhook_url: Optional[str] = None
    webhook_enabled: bool
    webhook_url: Optional[str] = None

    class Config:
        from_attributes = True

class CheckImportResponse(BaseModel):
    imported_count: int
    failed_count: int
    errors: list[str]


class CheckSimpleForStatusPage(BaseModel):
    id: int
    uuid: str
    name: str
    status: str
    paused: bool
    last_ping: Optional[datetime] = None
    last_duration_seconds: Optional[float] = None

    class Config:
        from_attributes = True


# Status Page Schemas
class StatusPageBase(BaseModel):
    name: str
    slug: str

class StatusPageCreate(StatusPageBase):
    check_ids: list[int] = []

class StatusPageUpdate(StatusPageBase):
    check_ids: list[int] = []

class StatusPage(StatusPageBase):
    id: int
    uuid: str
    is_public: bool
    owner_id: int
    checks: list[CheckSimpleForStatusPage]

    class Config:
        from_attributes = True

class StatusPagePublic(StatusPageBase):
    name: str
    checks: list[CheckSimpleForStatusPage]

    class Config:
        from_attributes = True


class PingLog(BaseModel):
    timestamp: datetime
    ip_address: str
    user_agent: str
    country_code: str
    country_name: str
    connection_type: str

class Check(CheckBase):
    id: int
    uuid: str
    status: str
    paused: bool
    created_at: datetime
    last_ping: Optional[datetime] = None
    last_start: Optional[datetime] = None
    last_duration_seconds: Optional[float] = None
    expected_content: Optional[str] = None
    expected_content_type: Optional[str] = None
    use_regex_for_content: bool
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool
    telegram_last_notification_status: Optional[str] = None
    telegram_last_notification_message: Optional[str] = None
    telegram_last_notification_timestamp: Optional[datetime] = None
    slack_webhook_url: Optional[str] = None
    slack_enabled: bool
    slack_last_notification_status: Optional[str] = None
    slack_last_notification_message: Optional[str] = None
    slack_last_notification_timestamp: Optional[datetime] = None
    discord_webhook_url: Optional[str] = None
    discord_enabled: bool
    discord_last_notification_status: Optional[str] = None
    discord_last_notification_message: Optional[str] = None
    discord_last_notification_timestamp: Optional[datetime] = None
    webhook_url: Optional[str] = None
    webhook_enabled: bool
    webhook_last_notification_status: Optional[str] = None
    webhook_last_notification_message: Optional[str] = None
    webhook_last_notification_timestamp: Optional[datetime] = None
    owner_id: int
    owner: Optional[User] = None # Include owner details
    last_pings: list[PingLog] = []
    last_content: Optional[str] = None

    class Config:
        from_attributes = True


class CheckPage(BaseModel):
    items: list[Check]
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    size: int


class CheckStats(BaseModel):
    total_checks: int
    up_count: int
    down_count: int
    new_count: int
    paused_count: int = 0
    avg_interval_seconds: Optional[float] = None
    avg_duration_seconds: Optional[float] = None
    user_queued_notifications: Union[int, str] = 0


# Telegram Schemas
class TelegramSettings(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = False

class TelegramSettingsUpdate(TelegramSettings):
    pass


class SlackSettings(BaseModel):
    slack_webhook_url: Optional[str] = None
    slack_enabled: bool = False

class SlackSettingsUpdate(SlackSettings):
    pass


class DiscordSettings(BaseModel):
    discord_webhook_url: Optional[str] = None
    discord_enabled: bool = False

class DiscordSettingsUpdate(DiscordSettings):
    pass


class WebhookSettings(BaseModel):
    webhook_url: Optional[str] = None
    webhook_enabled: bool = False

class WebhookSettingsUpdate(WebhookSettings):
    pass


class TelegramLoginData(BaseModel):
    id: int
    first_name: str
    username: Optional[str] = None
    auth_date: int
    hash: str
    photo_url: Optional[str] = None
