import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.security import create_token
from app.models import User
from tests.fakes.repositories import FakeUserRepository

PATH = "/api/v1/recommendations/people"


@pytest.fixture
def auth(users: FakeUserRepository, settings: Settings) -> dict[str, str]:
    users.items[1] = User(id=1, name="Первый", about=None, office_id=1, survey_completed=True)
    users.items[2] = User(id=2, name="Второй", about="Описание", office_id=1, survey_completed=True)
    return {"Authorization": f"Bearer {create_token(1, 'access', settings)}"}


async def test_requires_access_token_and_completed_survey(
    client: AsyncClient,
    users: FakeUserRepository,
    auth: dict[str, str],
    settings: Settings,
) -> None:
    response = await client.get(PATH)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    response = await client.get(
        PATH, headers={"Authorization": f"Bearer {create_token(1, 'refresh', settings)}"}
    )
    assert response.status_code == 401
    users.items[1].survey_completed = False
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "survey_required"


async def test_public_response_and_empty_result(
    client: AsyncClient,
    users: FakeUserRepository,
    auth: dict[str, str],
) -> None:
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 200
    assert response.json() == [
        {
            "user": {
                "id": 2,
                "name": "Второй",
                "about": "Описание",
                "office": {"id": 1, "city": "Екатеринбург", "name": "Офис на Ленина"},
                "interests": [],
                "free_answers": [],
            },
            "score": 0.15,
            "same_office": True,
            "common_interests": [],
        }
    ]
    users.items.pop(2)
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 200 and response.json() == []


@pytest.mark.parametrize("limit", ["0", "-1", "21", "1.5", "abc"])
async def test_invalid_limit(client: AsyncClient, auth: dict[str, str], limit: str) -> None:
    response = await client.get(PATH, params={"limit": limit}, headers=auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(("limit", "count"), [(None, 20), (1, 1), (20, 20)])
async def test_limit_default_and_boundaries(
    client: AsyncClient,
    users: FakeUserRepository,
    auth: dict[str, str],
    limit: int | None,
    count: int,
) -> None:
    for i in range(3, 25):
        users.items[i] = User(id=i, name="Коллега", about=None, office_id=1, survey_completed=True)
    response = await client.get(
        PATH, params={} if limit is None else {"limit": limit}, headers=auth
    )
    assert response.status_code == 200
    assert [row["user"]["id"] for row in response.json()] == list(range(2, count + 2))


async def test_openapi_matching(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    endpoint = schema["paths"][PATH]["get"]
    assert {"200", "401", "403", "422"} <= endpoint["responses"].keys()
    assert endpoint["security"] == [{"HTTPBearer": []}]
    limit = next(p["schema"] for p in endpoint["parameters"] if p["name"] == "limit")
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 20, 20)
    response = endpoint["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["type"] == "array"
    assert response["items"]["$ref"].endswith("/PersonRecommendationOut")
    public = schema["components"]["schemas"]["PublicProfile"]["properties"]
    assert set(public) == {"id", "name", "about", "office", "interests", "free_answers"}
