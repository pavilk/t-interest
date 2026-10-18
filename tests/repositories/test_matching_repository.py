import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Interest, Match, MatchStatus, Office, User, UserAnswer, UserInterest
from app.repositories.match import SqlMatchRepository
from app.repositories.survey import SqlSurveyRepository

pytestmark = pytest.mark.postgres


async def test_batch_active_text_answers(pg_data: async_sessionmaker[AsyncSession]) -> None:
    async with pg_data.begin() as session:
        session.add_all(
            [
                UserAnswer(user_id=1, question_id=3, text_value="Первый"),
                UserAnswer(user_id=2, question_id=3, text_value="Второй"),
                UserAnswer(user_id=2, question_id=4, text_value="Архив"),
                UserAnswer(user_id=2, question_id=1, option_id=1),
            ]
        )
    async with pg_data() as session:
        repo = SqlSurveyRepository(session)
        assert await repo.list_active_text_answers([]) == []
        rows = await repo.list_active_text_answers([2, 999])
        assert [(r.user_id, r.question_id, r.text_value) for r in rows] == [(2, 3, "Второй")]


@pytest.mark.parametrize("user_id", [1, 2])
@pytest.mark.parametrize("a_status", list(MatchStatus))
@pytest.mark.parametrize("b_status", list(MatchStatus))
async def test_excluded_users(
    pg_data: async_sessionmaker[AsyncSession],
    user_id: int,
    a_status: MatchStatus,
    b_status: MatchStatus,
) -> None:
    async with pg_data.begin() as session:
        session.add(
            Match(user_a_id=1, user_b_id=2, score=0.5, a_status=a_status, b_status=b_status)
        )
    async with pg_data() as session:
        own = a_status if user_id == 1 else b_status
        excluded = own == MatchStatus.ACCEPTED or MatchStatus.DECLINED in (a_status, b_status)
        assert await SqlMatchRepository(session).excluded_user_ids(user_id) == (
            {1, 2} if excluded else {user_id}
        )


async def test_candidate_eligibility_and_approved_tree(
    pg_data: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_data.begin() as session:
        session.add_all(
            [
                Office(id=2, name="Второй офис", city="Тестовый город"),
                Interest(id=20, name="Скрытое направление", is_approved=False),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Interest(id=21, name="Скрытый родитель", parent_id=20),
                Interest(id=22, name="Третий уровень", parent_id=11),
            ]
        )
        session.add_all(
            [
                User(
                    id=i,
                    email=f"candidate{i}@example.com",
                    password_hash="unused",
                    name=f"Кандидат {i}",
                    office_id=2,
                    survey_completed=i != 7,
                )
                for i in range(3, 10)
            ]
        )
        for user in (await session.scalars(select(User))).all():
            if user.id in (1, 2):
                user.survey_completed = True
        await session.flush()
        session.add_all(
            [
                UserInterest(user_id=3, interest_id=11),
                UserInterest(user_id=3, interest_id=12),
                UserInterest(user_id=4, interest_id=13),
                UserInterest(user_id=5, interest_id=21),
                UserInterest(user_id=6, interest_id=10),
                UserInterest(user_id=7, interest_id=11),
                UserInterest(user_id=8, interest_id=22),
            ]
        )
    async with pg_data() as session:
        repo = SqlSurveyRepository(session)
        # Same office OR an approved common leaf; neither city alone nor invalid trees.
        ids = await repo.list_matching_candidate_ids({10, 11, 12, 13, 21, 22}, 1, {1}, 500)
        assert list(ids) == [2, 3]
        assert list(await repo.list_matching_candidate_ids(set(), 1, {1}, 500)) == [2]
        assert list(await repo.list_matching_candidate_ids({11}, 1, {1, 2, 3}, 500)) == []


async def test_candidate_cap_applied_after_exclusions_in_id_order(
    pg_data: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_data.begin() as session:
        session.add_all(
            [
                User(
                    id=i,
                    email=f"cap{i}@example.com",
                    password_hash="unused",
                    name="Кандидат",
                    office_id=1,
                    survey_completed=True,
                )
                for i in range(3, 509)
            ]
        )
    async with pg_data() as session:
        ids = await SqlSurveyRepository(session).list_matching_candidate_ids(
            set(), 1, {1, 3, 4, 5}, 500
        )
        assert list(ids) == list(range(6, 506))
