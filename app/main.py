from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base
from app.db.base import engine, get_db
from app.api.v1.routes import api_router
from app.web.routes import router as web_router, admin_router
from app.services import scheduler
from app import crud

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting up...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    scheduler_task = asyncio.create_task(scheduler.check_jobs())
    
    yield
    
    print("Shutting down...")
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        print("Scheduler task cancelled.")


app = FastAPI(lifespan=lifespan, title="ttl.rip")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/ping/{uuid}", status_code=status.HTTP_200_OK)
async def ping_check(uuid: str, db: AsyncSession = Depends(get_db)):
    db_check = await crud.get_check_by_uuid(db, check_uuid=uuid)
    if not db_check:
        raise HTTPException(status_code=404, detail="Check not found")
    await crud.update_check_ping(db, check=db_check)
    return {"message": "OK"}

app.include_router(api_router, prefix="/api/v1")
app.include_router(web_router)
app.include_router(admin_router)
