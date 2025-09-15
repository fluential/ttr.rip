from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
