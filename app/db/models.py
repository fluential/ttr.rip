from datetime import datetime, timezone
from typing import Optional
import uuid
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Boolean,
    Table,
    Float,
)
from sqlalchemy.orm import relationship, DeclarativeBase

class Base(DeclarativeBase):
    pass

# Association Table for the many-to-many relationship between StatusPage and Check
status_page_checks = Table(
    "status_page_checks",
    Base.metadata,
    Column("status_page_id", Integer, ForeignKey("status_pages.id"), primary_key=True),
    Column("check_id", Integer, ForeignKey("checks.id"), primary_key=True),
)

# Association Table for the many-to-many relationship between Check and Tag
check_tags = Table(
    "check_tags",
    Base.metadata,
    Column("check_id", Integer, ForeignKey("checks.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)

class Tag(Base):
    __tablename__ = "tags"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String, index=True, nullable=False)
    owner_id: int = Column(Integer, ForeignKey("users.id"), nullable=False)

    owner = relationship("User", back_populates="tags")
    checks = relationship("Check", secondary=check_tags, back_populates="tags")


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

    checks = relationship("Check", back_populates="owner", cascade="all, delete-orphan")
    status_pages = relationship("StatusPage", back_populates="owner", cascade="all, delete-orphan")
    tags = relationship("Tag", back_populates="owner", cascade="all, delete-orphan")


class StatusPage(Base):
    __tablename__ = "status_pages"

    id: int = Column(Integer, primary_key=True, index=True)
    uuid: str = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    name: str = Column(String, index=True)
    slug: str = Column(String, unique=True, index=True)
    is_public: bool = Column(Boolean, default=True, nullable=False)
    owner_id: int = Column(Integer, ForeignKey("users.id"), nullable=False)

    owner = relationship("User", back_populates="status_pages")
    checks = relationship("Check", secondary=status_page_checks, back_populates="status_pages")


class Check(Base):
    __tablename__ = "checks"

    id: int = Column(Integer, primary_key=True, index=True)
    uuid: str = Column(String, unique=True, index=True)
    slug: Optional[str] = Column(String, unique=True, index=True, nullable=True)
    name: str = Column(String, index=True)
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Scheduling
    schedule: Optional[str] = Column(String, nullable=True)
    tz: str = Column(String, default="UTC", nullable=False)
    interval_seconds: Optional[int] = Column(Integer, nullable=True)
    grace_seconds: int = Column(Integer)

    deadline: Optional[datetime] = Column(DateTime(timezone=True), nullable=True, index=True)
    max_runtime_seconds: Optional[int] = Column(Integer, nullable=True)
    paused: bool = Column(Boolean, default=False, nullable=False)

    # Conditional Notifications
    notify_after_failures: Optional[int] = Column(Integer, nullable=True)
    notify_on_up: bool = Column(Boolean, default=True, nullable=False)

    # Content validation settings
    expected_content: Optional[str] = Column(String, nullable=True)
    expected_content_type: Optional[str] = Column(String, nullable=True) # 'present' or 'absent'
    use_regex_for_content: bool = Column(Boolean, default=False, nullable=False)

    telegram_bot_token: Optional[str] = Column(String, nullable=True)
    telegram_chat_id: Optional[str] = Column(String, nullable=True)
    telegram_enabled: bool = Column(Boolean, default=False, nullable=False)
    telegram_last_notification_status: Optional[str] = Column(String, nullable=True)
    telegram_last_notification_message: Optional[str] = Column(String, nullable=True)
    telegram_last_notification_timestamp: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    # Slack settings
    slack_webhook_url: Optional[str] = Column(String, nullable=True)
    slack_enabled: bool = Column(Boolean, default=False, nullable=False)
    slack_last_notification_status: Optional[str] = Column(String, nullable=True)
    slack_last_notification_message: Optional[str] = Column(String, nullable=True)
    slack_last_notification_timestamp: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    # Discord settings
    discord_webhook_url: Optional[str] = Column(String, nullable=True)
    discord_enabled: bool = Column(Boolean, default=False, nullable=False)
    discord_last_notification_status: Optional[str] = Column(String, nullable=True)
    discord_last_notification_message: Optional[str] = Column(String, nullable=True)
    discord_last_notification_timestamp: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    # Generic webhook settings
    webhook_url: Optional[str] = Column(String, nullable=True)
    webhook_enabled: bool = Column(Boolean, default=False, nullable=False)
    webhook_last_notification_status: Optional[str] = Column(String, nullable=True)
    webhook_last_notification_message: Optional[str] = Column(String, nullable=True)
    webhook_last_notification_timestamp: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)

    owner_id: int = Column(Integer, ForeignKey("users.id"), nullable=False)

    owner = relationship("User", back_populates="checks", lazy="selectin")
    status_pages = relationship("StatusPage", secondary=status_page_checks, back_populates="checks")
    tags = relationship("Tag", secondary=check_tags, back_populates="checks", lazy="selectin")
