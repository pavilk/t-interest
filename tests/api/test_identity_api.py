"""Проверяем HTTP-слой: коды ответов, формат ошибок, авторизацию.

Бизнес-правила уже покрыты в tests/unit — здесь только то, что добавляет роутер.
"""

from httpx import AsyncClient

REGISTER = {
    "email": "anya@example.com",
    "password": "correct-horse-battery",
    "name": "Аня",
    "office_id": 1,
}


async def register(client: AsyncClient) -> str:
    response = await client.post("/api/v1/auth/register", json=REGISTER)
    assert response.status_code == 201
    token: str = response.json()["access_token"]
    return token


async def test_register_then_get_me(client: AsyncClient) -> None:
    token = await register(client)

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "anya@example.com"
    assert body["office"]["city"] == "Екатеринбург"
    assert body["survey_completed"] is False


async def test_domain_error_has_common_format(client: AsyncClient) -> None:
    await register(client)

    response = await client.post("/api/v1/auth/register", json=REGISTER)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_validation_error_does_not_echo_input(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json={**REGISTER, "password": "Qx7#zPw"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "Qx7#zPw" not in response.text


async def test_me_requires_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_rejects_refresh_token(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=REGISTER)
    refresh = response.json()["refresh_token"]

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {refresh}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_login_wrong_password(client: AsyncClient) -> None:
    await register(client)

    response = await client.post(
        "/api/v1/auth/login", json={"email": REGISTER["email"], "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


async def test_list_offices(client: AsyncClient) -> None:
    token = await register(client)

    response = await client.get("/api/v1/offices", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == [{"id": 1, "city": "Екатеринбург", "name": "Офис на Ленина"}]


async def test_openapi_builds(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/auth/register" in response.json()["paths"]
