"""Alembic environment: async engine, URL and models from marginalia."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from marginalia.api.db import Base
from marginalia.config import Settings

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)
URL = context.config.attributes.get('url') or Settings().database_url


def run_offline():
    context.configure(url=URL, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
