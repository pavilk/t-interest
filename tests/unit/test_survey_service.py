from dataclasses import dataclass

import pytest

from app.core.config import Settings
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.models import (
    AnswerOption,
    Interest,
    Office,
    Question,
    QuestionType,
    User,
    UserAnswer,
    UserRole,
)
from app.schemas.survey import SurveyAnswer
from app.services.identity import IdentityService
from app.services.survey import SurveyService
from tests.fakes.repositories import (
    FakeInterestRepository,
    FakeOfficeRepository,
    FakeSurveyRepository,
    FakeUserRepository,
)


@dataclass
class Context:
    service: SurveyService
    surveys: FakeSurveyRepository
    interests: FakeInterestRepository
    users: FakeUserRepository


@pytest.fixture
async def ctx(settings: Settings) -> Context:
    users = FakeUserRepository()
    await users.add(
        User(
            email="test@example.com",
            name="Test",
            office_id=1,
            password_hash="unused",
            survey_completed=False,
            role=UserRole.USER,
        )
    )
    await users.add(
        User(
            email="other@example.com",
            name="Other",
            office_id=1,
            password_hash="unused",
            survey_completed=False,
            role=UserRole.USER,
        )
    )
    offices = FakeOfficeRepository([Office(id=1, city="Тестовый город", name="Офис")])
    surveys = FakeSurveyRepository(
        questions=[
            Question(id=1, text="Single", type=QuestionType.SINGLE, order=1, is_active=True),
            Question(id=2, text="Multi", type=QuestionType.MULTI, order=2, is_active=True),
            Question(id=3, text="Text", type=QuestionType.TEXT, order=3, is_active=True),
            Question(id=4, text="Archive", type=QuestionType.SINGLE, order=0, is_active=False),
        ],
        options=[
            AnswerOption(id=1, question_id=1, text="A", order=1, interest_id=11),
            AnswerOption(id=2, question_id=1, text="B", order=2, interest_id=12),
            AnswerOption(id=3, question_id=2, text="C", order=1, interest_id=11),
            AnswerOption(id=4, question_id=2, text="D", order=2, interest_id=None),
        ],
    )
    interests = FakeInterestRepository(
        [
            Interest(id=10, name="Спорт", parent_id=None, is_approved=True),
            Interest(id=11, name="Бег", parent_id=10, is_approved=True),
            Interest(id=12, name="Плавание", parent_id=10, is_approved=True),
            Interest(id=13, name="Скрытый", parent_id=10, is_approved=False),
        ]
    )
    return Context(
        SurveyService(surveys, interests, IdentityService(users, offices, settings)),
        surveys,
        interests,
        users,
    )


def valid() -> list[SurveyAnswer]:
    return [
        SurveyAnswer(question_id=1, option_ids=[1]),
        SurveyAnswer(question_id=2, option_ids=[3, 4]),
    ]


async def test_submit_replaces_answers_interests_and_completes(ctx: Context) -> None:
    result = await ctx.service.submit_answers(
        1, valid() + [SurveyAnswer(question_id=3, text_value="  Другое  ")]
    )
    assert result.survey_completed is True
    assert ctx.interests.user_interests[1] == {11}
    assert len(ctx.surveys.answers) == 4
    assert ctx.surveys.answers[-1].text_value == "Другое"


async def test_resubmit_preserves_archive_and_other_users(ctx: Context) -> None:
    await ctx.service.submit_answers(1, valid() + [SurveyAnswer(question_id=3, text_value="old")])
    archive = UserAnswer(user_id=1, question_id=4, option_id=None, text_value="archive")
    other = UserAnswer(user_id=2, question_id=1, option_id=1, text_value=None)
    ctx.surveys.answers.extend([archive, other])
    ctx.interests.user_interests[2] = {11}
    await ctx.service.submit_answers(
        1,
        [SurveyAnswer(question_id=1, option_ids=[2]), SurveyAnswer(question_id=2, option_ids=[4])],
    )
    assert ctx.interests.user_interests == {1: {12}, 2: {11}}
    assert archive in ctx.surveys.answers and other in ctx.surveys.answers
    assert len(ctx.surveys.answers) == 4
    answers = await ctx.service.get_answers(1)
    assert [a.question_id for a in answers] == [1, 2]
    assert answers[0].option_ids == [2]


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ([SurveyAnswer(question_id=99, option_ids=[1])], "unknown_question"),
        ([SurveyAnswer(question_id=4, option_ids=[1])], "unknown_question"),
        ([SurveyAnswer(question_id=1, option_ids=[3])], "option_not_in_question"),
        ([SurveyAnswer(question_id=1, option_ids=[999])], "option_not_in_question"),
        ([SurveyAnswer(question_id=1, option_ids=[])], "single_choice_violation"),
        ([SurveyAnswer(question_id=1, option_ids=[1, 2])], "single_choice_violation"),
        ([SurveyAnswer(question_id=2, option_ids=[])], "multi_choice_violation"),
        ([SurveyAnswer(question_id=2, option_ids=[3, 3])], "duplicate_option"),
        ([SurveyAnswer(question_id=1, option_ids=[1], text_value="mixed")], "invalid_answer_type"),
        ([SurveyAnswer(question_id=3, option_ids=[1], text_value="mixed")], "invalid_answer_type"),
        ([SurveyAnswer(question_id=3, text_value="   ")], "empty_text"),
        ([SurveyAnswer(question_id=3)], "empty_text"),
    ],
)
async def test_validation_does_not_write(
    ctx: Context, replacement: list[SurveyAnswer], code: str
) -> None:
    answers = [a for a in valid() if a.question_id != replacement[0].question_id] + replacement
    with pytest.raises(DomainValidationError) as exc:
        await ctx.service.submit_answers(1, answers)
    assert exc.value.code == code
    assert ctx.surveys.answers == []
    assert ctx.interests.user_interests == {}
    assert ctx.users.items[1].survey_completed is False


async def test_duplicate_question(ctx: Context) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await ctx.service.submit_answers(1, valid() + [valid()[0]])
    assert exc.value.code == "duplicate_question"


async def test_required_question_missing(ctx: Context) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await ctx.service.submit_answers(1, valid()[:1])
    assert exc.value.code == "required_question_missing"


async def test_internal_call_checks_text_limit(ctx: Context) -> None:
    answer = SurveyAnswer.model_construct(question_id=3, text_value="x" * 301)
    with pytest.raises(DomainValidationError) as exc:
        await ctx.service.submit_answers(1, valid() + [answer])
    assert exc.value.code == "text_too_long"


@pytest.mark.parametrize("completed", [False, True])
async def test_internal_call_rejects_nul_without_changing_state(
    ctx: Context, completed: bool
) -> None:
    if completed:
        await ctx.service.submit_answers(1, valid())
    previous_answers = list(ctx.surveys.answers)
    previous_interests = {key: set(value) for key, value in ctx.interests.user_interests.items()}
    answer = SurveyAnswer.model_construct(question_id=3, text_value="review\x00marker")
    changed = [
        SurveyAnswer(question_id=1, option_ids=[2]),
        SurveyAnswer(question_id=2, option_ids=[4]),
    ]

    with pytest.raises(DomainValidationError) as exc:
        await ctx.service.submit_answers(1, changed + [answer])

    assert exc.value.code == "validation_error"
    assert ctx.surveys.answers == previous_answers
    assert ctx.interests.user_interests == previous_interests
    assert ctx.users.items[1].survey_completed is completed


async def test_text_boundary_and_repeated_identical_put(ctx: Context) -> None:
    answers = valid() + [SurveyAnswer(question_id=3, text_value="x" * 300)]
    await ctx.service.submit_answers(1, answers)
    await ctx.service.submit_answers(1, answers)
    assert len(ctx.surveys.answers) == 4


async def test_empty_configuration_cannot_complete(ctx: Context) -> None:
    ctx.surveys.questions = []
    with pytest.raises(ConflictError) as exc:
        await ctx.service.submit_answers(1, [])
    assert exc.value.code == "survey_not_configured"
    assert ctx.users.items[1].survey_completed is False


@pytest.mark.parametrize("interest_id", [10, 13, 999])
async def test_invalid_interest_configuration(ctx: Context, interest_id: int) -> None:
    ctx.surveys.options[0].interest_id = interest_id
    with pytest.raises(ConflictError) as exc:
        await ctx.service.submit_answers(1, valid())
    assert exc.value.code == "survey_not_configured"
    assert not ctx.surveys.answers


async def test_survey_order_and_visible_interest_tree(ctx: Context) -> None:
    ctx.surveys.questions.reverse()
    ctx.surveys.options.reverse()
    survey = await ctx.service.get_survey()
    assert [q.id for q in survey] == [1, 2, 3]
    assert [o.id for o in survey[0].options] == [1, 2]
    tree = await ctx.service.list_interests()
    assert [i.id for i in tree] == [10]
    assert [i.id for i in tree[0].children] == [11, 12]


async def test_matching_profiles_empty_unknown_and_no_interests(ctx: Context) -> None:
    assert await ctx.service.get_matching_profile([]) == {}
    ctx.interests.user_interests[1] = {11}
    profiles = await ctx.service.get_matching_profile([1, 2, 999])
    assert set(profiles) == {1, 2}
    assert profiles[1].interest_ids == {11}
    assert profiles[1].office_id == 1
    assert profiles[1].city == "Тестовый город"
    assert profiles[2].interest_ids == set()


async def test_unknown_user_cannot_submit(ctx: Context) -> None:
    with pytest.raises(NotFoundError) as exc:
        await ctx.service.submit_answers(999, valid())
    assert exc.value.code == "user_not_found"
