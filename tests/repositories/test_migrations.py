import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.models import Base

pytestmark = pytest.mark.postgres


async def test_migrations_match_models_and_round_trip(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_factory() as session:
        connection = await session.connection()

        def verify(sync_connection):
            assert (
                compare_metadata(MigrationContext.configure(sync_connection), Base.metadata) == []
            )
            config = Config("alembic.ini")
            config.attributes["connection"] = sync_connection
            command.downgrade(config, "base")
            assert inspect(sync_connection).get_enums() == []
            command.upgrade(config, "head")
            assert (
                compare_metadata(MigrationContext.configure(sync_connection), Base.metadata) == []
            )

        await connection.run_sync(verify)
