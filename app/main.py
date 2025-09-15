from contextlib import asynccontextmanager
import asyncio
import logging
import time
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base
from app.db.base import engine, get_db, sql_query_times
from app.api.v1.routes import api_router
from app.web.routes import router as web_router, admin_router
from app.services import scheduler
from app import crud
from app.core.config import settings
from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    app.state.redis_connected = False
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    if not settings.DEBUG_MODE:
        try:
            import redis
            r = redis.from_url(str(settings.REDIS_URL))
            r.ping()
            logger.info("Successfully connected to Redis for Celery broker.")
            app.state.redis_connected = True
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")

    yield
    
    logger.info("Shutting down...")


app = FastAPI(lifespan=lifespan, title="ttl.rip")

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    sql_query_times.set([]) # Reset for new request
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    query_times = sql_query_times.get()
    total_sql_time = sum(query_times)
    num_queries = len(query_times)

    response.headers["X-Process-Time"] = str(process_time)
    response.headers["X-SQL-Time"] = str(total_sql_time)
    response.headers["X-SQL-Queries"] = str(num_queries)
    
    request.state.process_time = process_time
    request.state.sql_time = total_sql_time
    request.state.sql_queries = num_queries
    
    return response

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/ping/{uuid}", status_code=status.HTTP_200_OK)
async def ping_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_ping(db, check=db_check)
    return {"message": "OK"}


@app.get("/ping/{uuid}/start", status_code=status.HTTP_200_OK)
async def start_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_start(db, check=db_check)
    return {"message": "OK"}


@app.get("/ping/{uuid}/fail", status_code=status.HTTP_200_OK)
async def fail_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_fail(db, check=db_check)
    return {"message": "OK"}


app.include_router(api_router, prefix="/api/v1")
app.include_router(web_router)
app.include_router(admin_router)
