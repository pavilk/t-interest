from collections.abc import Sequence

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.security import create_token
from app.models import User
from tests.fakes.repositories import FakeMatchRepository, FakeUserRepository

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


async def test_user_removed_after_auth_returns_documented_404(
    client: AsyncClient,
    users: FakeUserRepository,
    auth: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = users.list_by_ids

    async def remove_current_user(user_ids: Sequence[int]) -> Sequence[User]:
        users.items.pop(1, None)
        return await original(user_ids)

    monkeypatch.setattr(users, "list_by_ids", remove_current_user)
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


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
    assert {"200", "401", "403", "404", "422"} <= endpoint["responses"].keys()
    assert endpoint["security"] == [{"HTTPBearer": []}]
    limit = next(p["schema"] for p in endpoint["parameters"] if p["name"] == "limit")
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 20, 20)
    response = endpoint["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["type"] == "array"
    assert response["items"]["$ref"].endswith("/PersonRecommendationOut")
    public = schema["components"]["schemas"]["PublicProfile"]["properties"]
    assert set(public) == {"id", "name", "about", "office", "interests", "free_answers"}


async def test_like_and_skip_flow_without_chat(
    client: AsyncClient, auth: dict[str, str], settings: Settings, matches: FakeMatchRepository
) -> None:
    peer_auth = {"Authorization": f"Bearer {create_token(2, 'access', settings)}"}
    skipped = await client.post("/api/v1/matches/2/skip", headers=auth)
    assert skipped.status_code == 204 and skipped.content == b""
    assert (await client.get(PATH, headers=auth)).json() == []
    assert [r["user"]["id"] for r in (await client.get(PATH, headers=peer_auth)).json()] == [1]
    for headers, target in [(peer_auth, 1), (auth, 2), (peer_auth, 1)]:
        liked = await client.post(f"/api/v1/matches/{target}/like", headers=headers)
        assert liked.status_code == 200
        assert liked.json() == {"matched": True, "chat_id": None}
        skipped = await client.post(f"/api/v1/matches/{target}/skip", headers=headers)
        assert skipped.status_code == 204
        assert (await client.get(PATH, headers=headers)).json() == []
    assert len(matches.items) == 1


@pytest.mark.parametrize("action", ["like", "skip"])
async def test_actions_require_access_token_and_survey(
    client: AsyncClient,
    users: FakeUserRepository,
    auth: dict[str, str],
    settings: Settings,
    matches: FakeMatchRepository,
    action: str,
) -> None:
    path = f"/api/v1/matches/2/{action}"
    response = await client.post(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    response = await client.post(
        path, headers={"Authorization": f"Bearer {create_token(1, 'refresh', settings)}"}
    )
    assert response.status_code == 401
    users.items[1].survey_completed = False
    response = await client.post(path, headers=auth)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "survey_required"
    assert matches.items == []


@pytest.mark.parametrize("action", ["like", "skip"])
@pytest.mark.parametrize(
    ("target", "status", "code"),
    [
        ("1", 422, "cannot_match_self"),
        ("999", 404, "user_not_found"),
        ("2", 409, "target_survey_required"),
        ("0", 422, "validation_error"),
        ("-1", 422, "validation_error"),
        ("abc", 422, "validation_error"),
        ("2147483647", 404, "user_not_found"),
        ("2147483648", 422, "validation_error"),
        ("9223372036854775808", 422, "validation_error"),
    ],
)
async def test_actions_reject_invalid_targets(
    client: AsyncClient,
    users: FakeUserRepository,
    auth: dict[str, str],
    matches: FakeMatchRepository,
    action: str,
    target: str,
    status: int,
    code: str,
) -> None:
    users.items[2].survey_completed = False
    response = await client.post(f"/api/v1/matches/{target}/{action}", headers=auth)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert matches.items == []


async def test_openapi_actions(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    for action, success in [("like", "200"), ("skip", "204")]:
        endpoint = schema["paths"][f"/api/v1/matches/{{user_id}}/{action}"]["post"]
        assert {success, "401", "403", "404", "409", "422"} <= endpoint["responses"].keys()
        assert endpoint["security"] == [{"HTTPBearer": []}]
        assert "requestBody" not in endpoint
        user_id = next(p["schema"] for p in endpoint["parameters"] if p["name"] == "user_id")
        assert (user_id["minimum"], user_id["maximum"]) == (1, 2147483647)
        if action == "skip":
            assert "content" not in endpoint["responses"][success]
    result = schema["components"]["schemas"]["LikeOut"]
    assert set(result["properties"]) == {"matched", "chat_id"}
    assert {t.get("type") for t in result["properties"]["chat_id"]["anyOf"]} == {"integer", "null"}
