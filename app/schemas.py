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
    interval_seconds: int
    grace_seconds: int

class CheckCreate(CheckBase):
    pass

class CheckUpdate(CheckBase):
    pass

class Check(CheckBase):
    id: int
    uuid: str
    status: str
    created_at: datetime
    last_ping: Optional[datetime] = None
    last_start: Optional[datetime] = None
    last_duration_seconds: Optional[int] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool
    telegram_last_notification_status: Optional[str] = None
    telegram_last_notification_message: Optional[str] = None
    telegram_last_notification_timestamp: Optional[datetime] = None
    owner_id: int
    owner: Optional[User] = None # Include owner details

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
    avg_interval_seconds: Optional[float] = None
    avg_duration_seconds: Optional[float] = None
    user_queued_notifications: Union[int, str] = 0
    processed_notifications: Union[int, str] = 0


# Telegram Schemas
class TelegramSettings(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = False

class TelegramSettingsUpdate(TelegramSettings):
    pass


class TelegramLoginData(BaseModel):
    id: int
    first_name: str
    username: Optional[str] = None
    auth_date: int
    hash: str
    photo_url: Optional[str] = None
