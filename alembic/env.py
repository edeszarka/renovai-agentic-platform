import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Set the sqlalchemy.url from the environment
db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models to ensure they are registered with Base.metadata
from renovai.db.models import Base

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

from renovai.db.session import get_engine

async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = context.config.attributes.get("connection", None)

    if connectable is None:
        db_url = config.get_main_option("sqlalchemy.url")
        connectable = get_engine(db_url)

    if isinstance(connectable, AsyncEngine):
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()
    else:
        with connectable.connect() as connection:
            do_run_migrations(connection)

if context.is_offline_mode():
    run_migrations_offline()
else:
    try:
        asyncio.run(run_migrations_online())
    except RuntimeError:
        # If there is already an event loop running, use it
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # This is tricky for a script, but for CLI it shouldn't happen usually
            # Just create a task if we are in a running loop
            import threading
            def run_in_new_loop():
                new_loop = asyncio.new_event_loop()
                new_loop.run_until_complete(run_migrations_online())
            t = threading.Thread(target=run_in_new_loop)
            t.start()
            t.join()
        else:
            loop.run_until_complete(run_migrations_online())
