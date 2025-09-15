from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.models import Base
from app.db.base import engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Welcome to ttl.rip"}
