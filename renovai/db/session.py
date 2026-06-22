import os
from typing import AsyncGenerator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from .models import Base, WorkCategory, SEED_WORK_CATEGORIES

def get_engine(database_url: str) -> AsyncEngine:
    """Creates an async SQLAlchemy engine."""
    is_sqlite = database_url.startswith("sqlite")
    
    engine_args = {
        "echo": False,
    }
    if is_sqlite:
        engine_args["connect_args"] = {"check_same_thread": False}
    else:
        engine_args["pool_size"] = 5
        engine_args["max_overflow"] = 10
        
    engine = create_async_engine(
        database_url,
        **engine_args
    )
    return engine

def get_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for async database sessions."""
    engine = get_engine(os.getenv("DATABASE_URL"))
    async_session = get_session_maker(engine)
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db(engine: AsyncEngine) -> None:
    """Creates tables and seeds lookup data."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async_session = get_session_maker(engine)
    async with async_session() as session:
        # Seed work categories
        res = await session.execute(select(WorkCategory))
        if not res.scalars().first():
            for key, hu, en, c_type in SEED_WORK_CATEGORIES:
                session.add(WorkCategory(key=key, label_hu=hu, label_en=en, cost_type=c_type))
            await session.commit()
