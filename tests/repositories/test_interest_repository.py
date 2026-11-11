import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import UserInterest
from app.repositories.interest import SqlInterestRepository

pytestmark = pytest.mark.postgres


async def test_replace_and_filter_interests(pg_data: async_sessionmaker[AsyncSession]) -> None:
    async with pg_data.begin() as session:
        repo = SqlInterestRepository(session)
        await repo.replace_user_interests(1, {11})
        await repo.replace_user_interests(2, {11})
        await repo.replace_user_interests(1, {12})
        assert await repo.get_user_interest_ids([1, 2, 999]) == {1: {12}, 2: {11}, 999: set()}
        # Bad historic/configuration data must not enter matching profiles.
        session.add_all(
            [UserInterest(user_id=1, interest_id=10), UserInterest(user_id=1, interest_id=13)]
        )
        await session.flush()
        assert (await repo.get_user_interest_ids([1]))[1] == {12}
        assert [i.id for i in await repo.list_approved()] == [11, 12, 10]
    async with pg_data() as session:
        assert len((await session.scalars(select(UserInterest))).all()) == 4
