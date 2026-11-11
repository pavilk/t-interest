"""HTTP-слой чатов: авторизация, коды ответов, формат. Правила — в tests/unit."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.services.chat import direct_key
from tests.fakes.repositories import FakeChatRepository, FakeUserRepository


async def login_as(
    client: AsyncClient, users: FakeUserRepository, name: str, survey: bool = True
) -> tuple[int, dict[str, str]]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{name}@example.com",
            "password": "correct-horse-battery",
            "name": name,
            "office_id": 1,
        },
    )
    assert response.status_code == 201
    user = await users.get_by_email(f"{name}@example.com")
    assert user is not None
    user.survey_completed = survey
    return user.id, {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def pair(
    client: AsyncClient, users: FakeUserRepository, chats: FakeChatRepository
) -> tuple[int, dict[str, str], dict[str, str]]:
    anya, anya_auth = await login_as(client, users, "anya")
    boris, boris_auth = await login_as(client, users, "boris")
    chats.user_names.update({anya: "anya", boris: "boris"})
    chat_id, _ = await chats.get_or_create_direct(direct_key(anya, boris), (anya, boris))
    return chat_id, anya_auth, boris_auth


ENDPOINTS = [
    ("GET", "/api/v1/chats", None),
    ("GET", "/api/v1/chats/1", None),
    ("GET", "/api/v1/chats/1/messages", None),
    ("POST", "/api/v1/chats/1/messages", {"text": "привет"}),
    ("POST", "/api/v1/chats/1/read", {"message_id": 1}),
]


@pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
async def test_requires_token(client: AsyncClient, method: str, url: str, body: object) -> None:
    response = await client.request(method, url, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
async def test_requires_survey(
    client: AsyncClient, users: FakeUserRepository, method: str, url: str, body: object
) -> None:
    _, auth = await login_as(client, users, "anya", survey=False)

    response = await client.request(method, url, json=body, headers=auth)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "survey_required"


async def test_foreign_chat_is_404(
    client: AsyncClient, users: FakeUserRepository, pair: tuple[int, dict[str, str], dict[str, str]]
) -> None:
    chat_id, _, _ = pair
    _, vera_auth = await login_as(client, users, "vera")

    response = await client.get(f"/api/v1/chats/{chat_id}", headers=vera_auth)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "chat_not_found"


async def test_send_list_and_read(
    client: AsyncClient, pair: tuple[int, dict[str, str], dict[str, str]]
) -> None:
    chat_id, anya_auth, boris_auth = pair
    client_msg_id = str(uuid4())

    sent = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"text": " привет ", "client_msg_id": client_msg_id},
        headers=anya_auth,
    )
    repeated = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"text": " привет ", "client_msg_id": client_msg_id},
        headers=anya_auth,
    )

    assert sent.status_code == 201
    message = sent.json()
    assert repeated.json() == message
    assert message["text"] == "привет"
    assert message["client_msg_id"] == client_msg_id
    assert set(message) == {"id", "chat_id", "sender", "text", "created_at", "client_msg_id"}

    listed = (await client.get("/api/v1/chats", headers=boris_auth)).json()
    assert [(c["id"], c["title"], c["unread_count"]) for c in listed] == [(chat_id, "anya", 1)]

    read = await client.post(
        f"/api/v1/chats/{chat_id}/read", json={"message_id": message["id"]}, headers=boris_auth
    )
    assert read.status_code == 204
    chat = (await client.get(f"/api/v1/chats/{chat_id}", headers=boris_auth)).json()
    assert chat["unread_count"] == 0

    page = await client.get(
        f"/api/v1/chats/{chat_id}/messages", params={"after_id": 0}, headers=boris_auth
    )
    assert page.json() == {"items": [message], "has_more": False}


async def test_both_cursors_is_422(
    client: AsyncClient, pair: tuple[int, dict[str, str], dict[str, str]]
) -> None:
    chat_id, anya_auth, _ = pair

    response = await client.get(
        f"/api/v1/chats/{chat_id}/messages",
        params={"before_id": 5, "after_id": 1},
        headers=anya_auth,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_empty_message_is_422(
    client: AsyncClient, pair: tuple[int, dict[str, str], dict[str, str]]
) -> None:
    chat_id, anya_auth, _ = pair

    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages", json={"text": "   "}, headers=anya_auth
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "message_empty"


async def test_rate_limit_is_429_in_common_format(
    client: AsyncClient, pair: tuple[int, dict[str, str], dict[str, str]]
) -> None:
    chat_id, anya_auth, _ = pair
    statuses = []
    for i in range(12):  # всплеск по умолчанию — 10
        response = await client.post(
            f"/api/v1/chats/{chat_id}/messages", json={"text": f"m{i}"}, headers=anya_auth
        )
        statuses.append(response.status_code)

    assert statuses[:10] == [201] * 10
    assert statuses[-1] == 429
    assert response.json()["error"]["code"] == "rate_limited"


async def test_openapi_contains_chat_schemas(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    for name in ("ChatOut", "MessageOut", "MessagePage", "SendMessageRequest"):
        assert name in schema["components"]["schemas"]
    send = schema["paths"]["/api/v1/chats/{chat_id}/messages"]["post"]["responses"]
    assert {"201", "404", "409", "422", "429"} <= set(send)
