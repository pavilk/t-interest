import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import User, UserAnswer
from app.repositories.survey import SqlSurveyRepository
from app.schemas.survey import SurveyAnswer
from tests.postgres import survey_service

pytestmark = pytest.mark.postgres


async def test_active_reads_and_archive_preserved(
    pg_data: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_data.begin() as session:
        repo = SqlSurveyRepository(session)
        assert [q.id for q in await repo.list_active_questions()] == [1, 2, 3]
        assert [o.id for o in await repo.list_options([1])] == [1, 2]
        session.add_all(
            [
                UserAnswer(user_id=1, question_id=4, text_value="archive"),
                UserAnswer(user_id=2, question_id=1, option_id=1),
            ]
        )
    async with pg_data.begin() as session:
        repo = SqlSurveyRepository(session)
        for _ in range(2):
            await repo.replace_active_answers(
                1, [1, 2, 3], [UserAnswer(user_id=1, question_id=1, option_id=2)]
            )
        assert [a.option_id for a in await repo.list_active_answers(1)] == [2]
    async with pg_data() as session:
        rows = (await session.scalars(select(UserAnswer))).all()
        assert len(rows) == 3
        assert any(a.question_id == 4 for a in rows)
        assert any(a.user_id == 2 for a in rows)


@pytest.mark.parametrize(
    "values",
    [
        {"option_id": None, "text_value": None},
        {"option_id": 1, "text_value": "both"},
    ],
)
async def test_xor_constraint(pg_data: async_sessionmaker[AsyncSession], values: dict) -> None:
    with pytest.raises(IntegrityError):
        async with pg_data.begin() as session:
            session.add(UserAnswer(user_id=1, question_id=1, **values))


async def test_unique_selected_option(pg_data: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(IntegrityError):
        async with pg_data.begin() as session:
            session.add_all([UserAnswer(user_id=1, question_id=1, option_id=1) for _ in range(2)])


async def test_concurrent_submissions_are_serialized(
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    first_written, second_started = asyncio.Event(), asyncio.Event()

    async def first() -> None:
        async with pg_data.begin() as session:
            await survey_service(session, settings).submit_answers(
                1,
                [
                    SurveyAnswer(question_id=1, option_ids=[1]),
                    SurveyAnswer(question_id=2, option_ids=[3]),
                ],
            )
            first_written.set()
            await second_started.wait()
            # Let the second transaction reach SELECT FOR UPDATE while the first holds it.
            await asyncio.sleep(0.1)

    async def second() -> None:
        await first_written.wait()
        async with pg_data.begin() as session:
            # Authentication may load a stale user before waiting on the lock.
            user = await session.get(User, 1)
            assert user is not None and not user.survey_completed
            second_started.set()
            await survey_service(session, settings).submit_answers(
                1,
                [
                    SurveyAnswer(question_id=1, option_ids=[2]),
                    SurveyAnswer(question_id=2, option_ids=[4]),
                ],
            )

    await asyncio.wait_for(asyncio.gather(first(), second()), timeout=10)
    async with pg_data() as session:
        service = survey_service(session, settings)
        answers = await service.get_answers(1)
        assert [a.option_ids for a in answers] == [[2], [4]]
        assert (await service.get_matching_profile([1]))[1].interest_ids == {12}
        assert await session.scalar(select(func.count()).select_from(UserAnswer)) == 2
