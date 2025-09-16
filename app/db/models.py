from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Boolean,
)
from sqlalchemy.orm import relationship, DeclarativeBase

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True, index=True)

    # For admin users
    username: Optional[str] = Column(String, unique=True, index=True, nullable=True)
    hashed_password: Optional[str] = Column(String, nullable=True)
    is_admin: bool = Column(Boolean, default=False, nullable=False)

    # For anonymous/telegram users
    auth_key: Optional[str] = Column(String, unique=True, index=True, nullable=True)

    # For telegram-linked users
    telegram_user_id: Optional[int] = Column(Integer, unique=True, index=True, nullable=True)
    telegram_first_name: Optional[str] = Column(String, nullable=True)
    telegram_username: Optional[str] = Column(String, nullable=True)

    checks = relationship("Check", back_populates="owner")


class Check(Base):
    __tablename__ = "checks"

    id: int = Column(Integer, primary_key=True, index=True)
    uuid: str = Column(String, unique=True, index=True)
    name: str = Column(String, index=True)
    status: str = Column(String, default="new")
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    interval_seconds: int = Column(Integer)
    grace_seconds: int = Column(Integer)
    last_ping: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    deadline: Optional[datetime] = Column(DateTime(timezone=True), nullable=True, index=True)
    last_start: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    last_duration_seconds: Optional[int] = Column(Integer, nullable=True)
    max_runtime_seconds: Optional[int] = Column(Integer, nullable=True)
    paused: bool = Column(Boolean, default=False, nullable=False)
    telegram_bot_token: Optional[str] = Column(String, nullable=True)
    telegram_chat_id: Optional[str] = Column(String, nullable=True)
    telegram_enabled: bool = Column(Boolean, default=False, nullable=False)
    telegram_last_notification_status: Optional[str] = Column(String, nullable=True)
    telegram_last_notification_message: Optional[str] = Column(String, nullable=True)
    telegram_last_notification_timestamp: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    owner_id: int = Column(Integer, ForeignKey("users.id"), nullable=False)

    owner = relationship("User", back_populates="checks", lazy="selectin")
