import pytest

from app.core.config import Settings
from app.core.errors import ConflictError, DomainValidationError
from app.models import AnswerOption, Office, Question, QuestionType, User, UserRole
from app.schemas.survey import SurveyAnswer
from app.services.identity import IdentityService
from app.services.survey import SurveyService
from tests.fakes.repositories import (
    FakeInterestRepository,
    FakeOfficeRepository,
    FakeSurveyRepository,
    FakeUserRepository,
)


@pytest.fixture
async def conditional(settings: Settings) -> tuple[SurveyService, FakeSurveyRepository]:
    users = FakeUserRepository()
    await users.add(
        User(
            email="conditional@example.com",
            name="Test",
            office_id=1,
            password_hash="unused",
            survey_completed=False,
            role=UserRole.USER,
        )
    )
    questions = [
        Question(
            id=1,
            code="directions",
            text="Directions",
            type=QuestionType.MULTI,
            order=1,
            stage=1,
            is_active=True,
            is_required=True,
        ),
        Question(
            id=2,
            code="sport",
            text="Sport",
            type=QuestionType.MULTI,
            order=2,
            stage=2,
            is_active=True,
            is_required=True,
            show_if_option_id=1,
        ),
        Question(
            id=3,
            code="sport_other",
            text="Other sport",
            type=QuestionType.TEXT,
            order=3,
            stage=2,
            is_active=True,
            is_required=True,
            show_if_option_id=3,
        ),
        Question(
            id=4,
            code="animal",
            text="Animal",
            type=QuestionType.TEXT,
            order=4,
            stage=2,
            is_active=True,
            is_required=False,
            show_if_option_id=2,
        ),
    ]
    repo = FakeSurveyRepository(
        questions,
        [
            AnswerOption(id=1, question_id=1, text="Sport", order=1),
            AnswerOption(id=2, question_id=1, text="Animals", order=2),
            AnswerOption(id=3, question_id=2, text="Other", order=1),
            AnswerOption(id=4, question_id=2, text="Run", order=2),
        ],
    )
    return SurveyService(
        repo,
        FakeInterestRepository(),
        IdentityService(
            users, FakeOfficeRepository([Office(id=1, city="Test", name="Test")]), settings
        ),
    ), repo


async def test_unselected_required_branch_is_not_required(conditional):
    service, repo = conditional
    await service.submit_answers(1, [SurveyAnswer(question_id=1, option_ids=[2])])
    assert [r.question_id for r in repo.answers] == [1]


async def test_other_requires_text_and_resubmit_clears_deselected_branch(conditional):
    service, repo = conditional
    selected = [
        SurveyAnswer(question_id=1, option_ids=[1]),
        SurveyAnswer(question_id=2, option_ids=[3, 4]),
    ]
    with pytest.raises(DomainValidationError) as error:
        await service.submit_answers(1, selected)
    assert error.value.code == "required_question_missing"
    assert repo.answers == []
    await service.submit_answers(1, selected + [SurveyAnswer(question_id=3, text_value="  Ski  ")])
    assert repo.answers[-1].text_value == "Ski"
    await service.submit_answers(1, [SurveyAnswer(question_id=1, option_ids=[2])])
    assert [r.question_id for r in repo.answers] == [1]


async def test_hidden_answer_cannot_enable_its_descendants(conditional):
    service, repo = conditional
    with pytest.raises(DomainValidationError) as error:
        await service.submit_answers(
            1,
            [
                SurveyAnswer(question_id=1, option_ids=[2]),
                SurveyAnswer(question_id=2, option_ids=[3]),
                SurveyAnswer(question_id=3, text_value="hidden"),
            ],
        )
    assert error.value.code == "question_not_applicable"
    assert repo.answers == []


@pytest.mark.parametrize("fault", ["missing", "cycle", "inactive", "text_parent"])
async def test_invalid_condition_configuration_is_rejected(conditional, fault):
    service, repo = conditional
    if fault == "missing":
        repo.questions[1].show_if_option_id = 999
    elif fault == "cycle":
        repo.questions[0].show_if_option_id = 3
    elif fault == "inactive":
        repo.questions[0].is_active = False
    else:
        repo.options[0].question_id = 4
    with pytest.raises(ConflictError) as error:
        await service.submit_answers(1, [SurveyAnswer(question_id=1, option_ids=[2])])
    assert error.value.code == "survey_not_configured"
    assert repo.answers == []


async def test_get_survey_exposes_conditions_and_all_branches(conditional):
    service, _ = conditional
    result = await service.get_survey()
    assert len(result) == 4
    assert result[1].model_dump() == {
        "id": 2,
        "code": "sport",
        "text": "Sport",
        "type": "multi",
        "order": 2,
        "stage": 2,
        "is_required": True,
        "show_if_option_id": 1,
        "options": [
            {"id": 3, "text": "Other", "order": 1, "interest_id": None},
            {"id": 4, "text": "Run", "order": 2, "interest_id": None},
        ],
    }
