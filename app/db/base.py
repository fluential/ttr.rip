import time
import contextvars
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings

# This context var will be set by the middleware for each request
sql_query_times: contextvars.ContextVar[list] = contextvars.ContextVar("sql_query_times", default=[])

engine = create_async_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

@event.listens_for(engine.sync_engine, "before_cursor_execute", named=True)
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.monotonic())

@event.listens_for(engine.sync_engine, "after_cursor_execute", named=True)
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total = time.monotonic() - conn.info["query_start_time"].pop(-1)
    times = sql_query_times.get()
    times.append(total)
    sql_query_times.set(times)

AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
