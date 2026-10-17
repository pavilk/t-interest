import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AnswerOption, Interest, Office, Question
from seeds.__main__ import seed

pytestmark = pytest.mark.postgres


async def snapshot(session: AsyncSession) -> dict[str, list[tuple]]:
    return {
        model.__tablename__: [
            (row.id, row.text if hasattr(row, "text") else row.name)
            for row in (await session.scalars(select(model).order_by(model.id))).all()
        ]
        for model in (Office, Interest, Question, AnswerOption)
    }


async def test_seeds_repeat_without_duplicates_or_reactivation(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_factory.begin() as session:
        await seed(session)
    async with pg_factory.begin() as session:
        before = await snapshot(session)
        question = await session.scalar(select(Question).order_by(Question.id))
        assert question is not None
        question.is_active = False
        question_id = question.id
    async with pg_factory.begin() as session:
        await seed(session)
    async with pg_factory() as session:
        assert await snapshot(session) == before
        question = await session.get(Question, question_id)
        assert question is not None and not question.is_active
        assert len(before["questions"]) == 3
        assert len(before["interests"]) == 6
