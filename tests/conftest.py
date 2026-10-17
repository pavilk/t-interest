import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: тесты не зависят от локального .env
    return Settings(
        _env_file=None,
        jwt_secret="test-secret-at-least-32-bytes-long!!",
        allowed_email_domains="example.com",
    )


@pytest.fixture
async def pg_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL to a separate PostgreSQL 16 test database")
    working_url = os.environ.get("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")
    target = make_url(url)
    if target.get_backend_name() != "postgresql" or "test" not in (target.database or "").lower():
        pytest.fail("TEST_DATABASE_URL must point to a PostgreSQL database named with 'test'")
    if working_url:
        working = make_url(working_url)
        if (target.host, target.port or 5432, target.database) == (
            working.host,
            working.port or 5432,
            working.database,
        ):
            pytest.fail("The test database must differ from the working database")
    # Every test owns a fresh schema, including its enums and Alembic version table.
    schema = "survey_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:

            def migrate(sync_connection: object) -> None:
                config = Config("alembic.ini")
                config.attributes["connection"] = sync_connection
                command.upgrade(config, "head")

            await connection.run_sync(migrate)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
async def pg_data(pg_factory: async_sessionmaker[AsyncSession]) -> async_sessionmaker[AsyncSession]:
    from tests.postgres import populate

    await populate(pg_factory)
    return pg_factory
