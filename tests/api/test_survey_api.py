import pytest
from httpx import AsyncClient

from app.models import AnswerOption, Interest, Question, QuestionType
from tests.api.test_identity_api import register
from tests.fakes.repositories import FakeInterestRepository, FakeSurveyRepository


@pytest.fixture(autouse=True)
def survey_data(surveys: FakeSurveyRepository, interests: FakeInterestRepository) -> None:
    surveys.questions = [
        Question(id=1, text="Выберите интерес", type=QuestionType.SINGLE, order=1, is_active=True),
        Question(id=2, text="Другое", type=QuestionType.TEXT, order=2, is_active=True),
    ]
    surveys.options = [AnswerOption(id=1, question_id=1, text="Бег", order=1, interest_id=11)]
    interests.items = {
        10: Interest(id=10, name="Спорт", parent_id=None, is_approved=True),
        11: Interest(id=11, name="Бег", parent_id=10, is_approved=True),
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/survey"),
        ("GET", "/survey/answers"),
        ("PUT", "/survey/answers"),
        ("GET", "/interests"),
    ],
)
async def test_survey_requires_auth(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(
        method, "/api/v1" + path, **({"json": []} if method == "PUT" else {})
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


async def test_onboarding_and_answers(client: AsyncClient) -> None:
    token = await register(client)
    headers = {"Authorization": f"Bearer {token}"}
    survey = await client.get("/api/v1/survey", headers=headers)
    assert survey.status_code == 200
    assert survey.json()[0]["options"][0]["interest_id"] == 11
    assert (await client.get("/api/v1/survey/answers", headers=headers)).json() == []
    response = await client.put(
        "/api/v1/survey/answers", headers=headers, json=[{"question_id": 1, "option_ids": [1]}]
    )
    assert response.status_code == 200
    assert response.json()["survey_completed"] is True
    assert (await client.get("/api/v1/me", headers=headers)).json()["survey_completed"] is True
    answers = (await client.get("/api/v1/survey/answers", headers=headers)).json()
    assert answers == [{"question_id": 1, "option_ids": [1], "text_value": None}]
    tree = (await client.get("/api/v1/interests", headers=headers)).json()
    assert tree == [{"id": 10, "name": "Спорт", "children": [{"id": 11, "name": "Бег"}]}]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"answers": []}, "validation_error"),
        ([{"question_id": 1, "option_ids": [0]}], "validation_error"),
        ([{"question_id": 1, "option_ids": [1], "user_id": 7}], "validation_error"),
        ([], "required_question_missing"),
        (
            [{"question_id": 1, "option_ids": [1]}, {"question_id": 2, "text_value": "s" * 301}],
            "text_too_long",
        ),
    ],
)
async def test_errors_have_common_format(client: AsyncClient, body: object, code: str) -> None:
    token = await register(client)
    response = await client.put(
        "/api/v1/survey/answers", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert "s" * 301 not in response.text


async def test_openapi_contract(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    put = schema["paths"]["/api/v1/survey/answers"]["put"]
    assert put["requestBody"]["content"]["application/json"]["schema"]["type"] == "array"
    assert put["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/MeOut"
    )
    assert {"401", "409", "422"} <= put["responses"].keys()
    text = schema["components"]["schemas"]["SurveyAnswer"]["properties"]["text_value"]
    assert any(branch.get("maxLength") == 300 for branch in text["anyOf"])
