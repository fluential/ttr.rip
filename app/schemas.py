from pydantic import BaseModel
from typing import Optional
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
    username: str

class UserCreate(UserBase):
    password: str
    is_admin: bool = False

class User(UserBase):
    id: int
    class Config:
        from_attributes = True

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
    owner_id: Optional[int] = None
    owner_key: Optional[str] = None

    class Config:
        from_attributes = True
