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
    username: str = Column(String, unique=True, index=True)
    hashed_password: str = Column(String)
    is_admin: bool = Column(Boolean, default=False)

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
    last_start: Optional[datetime] = Column(DateTime(timezone=True), nullable=True)
    last_duration_seconds: Optional[int] = Column(Integer, nullable=True)
    owner_id: Optional[int] = Column(Integer, ForeignKey("users.id"), nullable=True)
    owner_key: Optional[str] = Column(String, index=True, nullable=True)

    owner = relationship("User", back_populates="checks")
