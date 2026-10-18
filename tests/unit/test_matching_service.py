from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import (
    Interest,
    Match,
    MatchStatus,
    Office,
    Question,
    QuestionType,
    User,
    UserAnswer,
)
from app.schemas.user import PublicIdentityOut
from app.services.identity import IdentityService
from app.services.matching import MatchingService
from app.services.survey import SurveyService
from tests.fakes.repositories import (
    FakeInterestRepository,
    FakeMatchRepository,
    FakeOfficeRepository,
    FakeSurveyRepository,
    FakeUserRepository,
)


@dataclass
class Context:
    service: MatchingService
    users: FakeUserRepository
    interests: FakeInterestRepository
    surveys: FakeSurveyRepository
    matches: FakeMatchRepository
    identity: IdentityService


@pytest.fixture
def ctx(settings: Settings) -> Context:
    users = FakeUserRepository()
    for i, office_id in [(1, 1), (5, 1), (4, 3), (3, 2), (2, 1), (6, 2), (7, 1)]:
        users.items[i] = User(
            id=i, name=f"Персона {i}", about=None, office_id=office_id, survey_completed=i != 7
        )
    offices = FakeOfficeRepository(
        [
            Office(id=1, name="Первый", city="Город"),
            Office(id=2, name="Второй", city="Город"),
            Office(id=3, name="Третий", city="Другой"),
        ]
    )
    interests = FakeInterestRepository(
        [
            Interest(id=10, name="Направление", parent_id=None, is_approved=True),
            Interest(id=11, name="Бег", parent_id=10, is_approved=True),
            Interest(id=12, name="Акварель", parent_id=10, is_approved=True),
        ]
    )
    interests.user_interests = {i: {11} for i in range(1, 5)}
    surveys = FakeSurveyRepository(users=users, interests=interests)
    identity = IdentityService(users, offices, settings)
    matches = FakeMatchRepository()
    return Context(
        MatchingService(matches, SurveyService(surveys, interests, identity), identity),
        users,
        interests,
        surveys,
        matches,
        identity,
    )


async def test_missing_current_user_has_domain_error(ctx: Context) -> None:
    with pytest.raises(NotFoundError) as exc:
        await ctx.service.recommend_people(999, 20)
    assert exc.value.code == "user_not_found"


async def test_limit_is_applied_after_ranking(ctx: Context) -> None:
    ctx.interests.user_interests[2] = set()
    ctx.interests.user_interests[5] = {11}
    assert [r.user.id for r in await ctx.service.recommend_people(1, 1)] == [5]
    assert [r.user.id for r in await ctx.service.recommend_people(1, 2)] == [5, 3]


@pytest.mark.parametrize("change", ["interests", "office"])
async def test_concurrent_profile_change_keeps_score_and_explanation_consistent(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    original = ctx.identity.get_public_profiles

    async def change_before_public_read(user_ids: Sequence[int]) -> dict[int, PublicIdentityOut]:
        # A concurrent profile/answers update commits between the service's reads.
        if change == "interests":
            ctx.interests.user_interests[2] = {12}
        else:
            ctx.users.items[2].office_id = 2
        return await original(user_ids)

    monkeypatch.setattr(ctx.identity, "get_public_profiles", change_before_public_read)
    row = (await ctx.service.recommend_people(1, 1))[0]
    assert row.user.id == (3 if change == "interests" else 2)
    assert row.score == 1.05
    assert row.same_office is False
    assert row.user.office.id == 2
    assert [i.id for i in row.common_interests] == [11]


async def test_rank_then_limit_with_location_explanations(ctx: Context) -> None:
    rows = await ctx.service.recommend_people(1, 20)
    assert [(r.user.id, r.score, r.same_office) for r in rows] == [
        (2, 1.15, True),
        (3, 1.05, False),
        (4, 1.0, False),
        (5, 0.15, True),
    ]
    assert [i.id for i in rows[0].common_interests] == [11]
    assert rows[-1].common_interests == []
    assert [r.user.id for r in await ctx.service.recommend_people(1, 2)] == [2, 3]
    assert ctx.matches.items == []


async def test_ties_sort_by_id_and_empty_interests_keep_office(ctx: Context) -> None:
    ctx.interests.user_interests = {}
    rows = await ctx.service.recommend_people(1, 20)
    assert [(r.user.id, r.score) for r in rows] == [(2, 0.15), (5, 0.15)]
    ctx.users.items = {1: ctx.users.items[1]}
    assert await ctx.service.recommend_people(1, 20) == []


@pytest.mark.parametrize("user_id", [1, 2])
@pytest.mark.parametrize("a_status", list(MatchStatus))
@pytest.mark.parametrize("b_status", list(MatchStatus))
async def test_reactions_filter_both_pair_orientations(
    ctx: Context,
    user_id: int,
    a_status: MatchStatus,
    b_status: MatchStatus,
) -> None:
    ctx.matches.items = [Match(user_a_id=1, user_b_id=2, a_status=a_status, b_status=b_status)]
    own_status = a_status if user_id == 1 else b_status
    excluded = own_status == MatchStatus.ACCEPTED or MatchStatus.DECLINED in (a_status, b_status)
    ids = {r.user.id for r in await ctx.service.recommend_people(user_id, 20)}
    assert user_id not in ids
    assert ((3 - user_id) in ids) is not excluded


async def test_public_details_only_active_text_and_approved_leaves(ctx: Context) -> None:
    ctx.interests.items[13] = Interest(id=13, name="Скрытый", parent_id=10, is_approved=False)
    ctx.interests.user_interests[2] = {10, 11, 12, 13}
    ctx.surveys.questions = [
        Question(id=1, text="Поздний", type=QuestionType.TEXT, order=2, is_active=True),
        Question(id=2, text="Ранний", type=QuestionType.TEXT, order=1, is_active=True),
        Question(id=3, text="Архив", type=QuestionType.TEXT, order=0, is_active=False),
        Question(id=4, text="Выбор", type=QuestionType.SINGLE, order=0, is_active=True),
    ]
    ctx.surveys.answers = [
        UserAnswer(user_id=2, question_id=1, text_value="Один"),
        UserAnswer(user_id=2, question_id=2, text_value="Два"),
        UserAnswer(user_id=2, question_id=3, text_value="Скрытый ответ"),
        UserAnswer(user_id=2, question_id=4, option_id=99),
        UserAnswer(user_id=1, question_id=1, text_value="Чужой ответ"),
    ]
    row = next(r for r in await ctx.service.recommend_people(1, 20) if r.user.id == 2)
    assert [i.id for i in row.user.interests] == [12, 11]
    assert [i.id for i in row.common_interests] == [11]
    assert [a.model_dump() for a in row.user.free_answers] == [
        {"question_id": 2, "question_text": "Ранний", "text_value": "Два"},
        {"question_id": 1, "question_text": "Поздний", "text_value": "Один"},
    ]
    assert set(row.user.model_dump()) == {
        "id",
        "name",
        "about",
        "office",
        "interests",
        "free_answers",
    }
