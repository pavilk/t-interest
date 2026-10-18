"""WebSocket /api/v1/ws на фейках: рукопожатие, доставка, ошибки, таймауты, логи."""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi.requests import HTTPConnection
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings, get_settings
from app.core.deps import (
    get_chat_events,
    get_chat_repository,
    get_chat_units,
    get_office_repository,
    get_rate_limiter,
    get_user_repository,
)
from app.core.rate_limit import TokenBucketLimiter
from app.core.security import create_token
from app.main import create_app
from app.models import Office, User, UserRole
from app.services.chat import direct_key
from app.services.notifier import ChatEvent
from tests.fakes.repositories import FakeChatRepository, FakeOfficeRepository, FakeUserRepository
from tests.fakes.units import FakeChatUnits

WS = "/api/v1/ws"
ANYA, BORIS, VERA, NEWBIE = 1, 2, 3, 4


@pytest.fixture
def ws_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"ws_auth_timeout_seconds": 0.3, "ws_idle_timeout_seconds": 0.5}
    )


@pytest.fixture
async def users() -> FakeUserRepository:
    repo = FakeUserRepository()
    for name, survey in (("Аня", True), ("Борис", True), ("Вера", True), ("Новичок", False)):
        await repo.add(
            User(
                email=f"{name}@example.com",
                password_hash="x",
                name=name,
                office_id=1,
                role=UserRole.USER,
                survey_completed=survey,
            )
        )
    return repo


@pytest.fixture
def chats() -> FakeChatRepository:
    return FakeChatRepository(user_names={ANYA: "Аня", BORIS: "Борис", VERA: "Вера"})


@pytest.fixture
async def chat_id(chats: FakeChatRepository) -> int:
    created, _ = await chats.get_or_create_direct(direct_key(ANYA, BORIS), (ANYA, BORIS))
    return created


@pytest.fixture
def client(
    users: FakeUserRepository, chats: FakeChatRepository, ws_settings: Settings
) -> Iterator[TestClient]:
    app = create_app()
    offices = FakeOfficeRepository([Office(id=1, city="Екатеринбург", name="Офис")])
    limiter = TokenBucketLimiter(ws_settings.chat_rate_per_second, ws_settings.chat_rate_burst)

    class ImmediateEvents:
        """REST без БД: события сразу уходят в менеджер, как после commit."""

        def add(self, event: ChatEvent) -> None:
            app.state.connections.publish(event)

    def units(conn: HTTPConnection) -> FakeChatUnits:
        return FakeChatUnits(
            users, offices, chats, conn.app.state.connections, limiter, ws_settings
        )

    app.dependency_overrides[get_settings] = lambda: ws_settings
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_office_repository] = lambda: offices
    app.dependency_overrides[get_chat_repository] = lambda: chats
    app.dependency_overrides[get_chat_events] = ImmediateEvents
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_chat_units] = units
    with TestClient(app) as client:
        yield client


def token(user_id: int, settings: Settings) -> str:
    return create_token(user_id, "access", settings)


Connect = Callable[[int], WebSocketTestSession]


@pytest.fixture
def connect(client: TestClient, ws_settings: Settings) -> Iterator[Connect]:
    """Авторизованное соединение; все закрываются в конце теста, до остановки клиента."""
    with ExitStack() as stack:

        def open_ws(user_id: int) -> WebSocketTestSession:
            ws = stack.enter_context(client.websocket_connect(WS))
            ws.send_json({"type": "auth", "token": token(user_id, ws_settings)})
            assert ws.receive_json() == {"type": "auth_ok", "user_id": user_id}
            return ws

        yield open_ws


def close_code(ws: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    return exc.value.code


def send_frame(chat_id: int, text: str = "привет") -> dict[str, Any]:
    return {"type": "send", "chat_id": chat_id, "text": text, "client_msg_id": str(uuid4())}


# --- рукопожатие ---


def test_no_auth_in_time_closes_4401(client: TestClient) -> None:
    with client.websocket_connect(WS) as ws:
        assert close_code(ws) == 4401


@pytest.mark.parametrize(
    "first_frame",
    [{"type": "auth", "token": "garbage"}, {"type": "ping"}, {"nonsense": True}],
)
def test_bad_first_frame_closes_4401(client: TestClient, first_frame: dict[str, Any]) -> None:
    with client.websocket_connect(WS) as ws:
        ws.send_json(first_frame)
        assert close_code(ws) == 4401


def test_survey_not_completed_closes_4403(
    connect: Connect, client: TestClient, ws_settings: Settings
) -> None:
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "auth", "token": token(NEWBIE, ws_settings)})
        assert close_code(ws) == 4403


def test_expired_token_closes_connection(client: TestClient, ws_settings: Settings) -> None:
    expires = int(time.time()) + 2  # exp в JWT — целые секунды
    short = jwt.encode(
        {"sub": str(ANYA), "type": "access", "exp": expires},
        ws_settings.jwt_secret,
        algorithm=ws_settings.jwt_algorithm,
    )
    with client.websocket_connect(WS) as ws:
        ws.send_json({"type": "auth", "token": short})
        assert ws.receive_json()["type"] == "auth_ok"
        # пинги не дают сработать idle-таймауту, но и не продлевают жизнь токена
        with pytest.raises(WebSocketDisconnect) as exc:
            for _ in range(20):
                ws.send_json({"type": "ping"})
                assert ws.receive_json() == {"type": "pong"}
                time.sleep(0.2)
        assert exc.value.code == 4401
        assert time.time() >= expires


# --- доставка ---


def test_send_delivers_message_to_peer_and_ack_to_sender(connect: Connect, chat_id: int) -> None:
    anya = connect(ANYA)
    boris = connect(BORIS)
    frame = send_frame(chat_id, "  привет  ")

    anya.send_json(frame)

    received = boris.receive_json()
    assert received["type"] == "message"
    assert received["message"]["text"] == "привет"
    assert received["message"]["sender"] == {"id": ANYA, "name": "Аня"}
    anya_frames = {f["type"]: f for f in (anya.receive_json(), anya.receive_json())}
    assert anya_frames["ack"]["client_msg_id"] == frame["client_msg_id"]
    assert anya_frames["ack"]["message"] == anya_frames["message"]["message"]
    assert anya_frames["message"]["message"]["id"] == received["message"]["id"]


def test_repeated_send_acks_same_message_without_rebroadcast(
    connect: Connect,
    client: TestClient,
    ws_settings: Settings,
    chat_id: int,
    chats: FakeChatRepository,
) -> None:
    anya = connect(ANYA)
    boris = connect(BORIS)
    frame = send_frame(chat_id)

    anya.send_json(frame)
    first = boris.receive_json()
    anya.receive_json()
    anya.receive_json()
    anya.send_json(frame)  # повтор после «обрыва»
    ack = anya.receive_json()

    assert ack["type"] == "ack" and ack["message"]["id"] == first["message"]["id"]
    assert len(chats.messages) == 1
    boris.send_json({"type": "ping"})
    assert boris.receive_json() == {"type": "pong"}  # второго message не было


def test_send_to_foreign_chat_is_error_and_delivers_nothing(
    connect: Connect,
    client: TestClient,
    ws_settings: Settings,
    chat_id: int,
    chats: FakeChatRepository,
) -> None:
    vera = connect(VERA)
    boris = connect(BORIS)
    frame = send_frame(chat_id)

    vera.send_json(frame)

    error = vera.receive_json()
    assert error["type"] == "error"
    assert error["code"] == "chat_not_found"
    assert error["client_msg_id"] == frame["client_msg_id"]
    assert error["chat_id"] == chat_id
    assert chats.messages == []
    boris.send_json({"type": "ping"})
    assert boris.receive_json() == {"type": "pong"}


@pytest.mark.parametrize("raw", ["not json", '{"type": "unknown"}', '{"type": "send"}'])
def test_bad_frame_keeps_connection_alive(
    connect: Connect, client: TestClient, ws_settings: Settings, raw: str
) -> None:
    anya = connect(ANYA)

    anya.send_text(raw)

    assert anya.receive_json() == {
        "type": "error",
        "code": "bad_frame",
        "message": "Некорректный фрейм",
        "client_msg_id": None,
        "chat_id": None,
    }
    anya.send_json({"type": "ping"})
    assert anya.receive_json() == {"type": "pong"}


def test_read_syncs_other_tabs_of_same_user(connect: Connect, chat_id: int) -> None:
    anya = connect(ANYA)
    boris_tab1 = connect(BORIS)
    boris_tab2 = connect(BORIS)
    anya.send_json(send_frame(chat_id))
    message_id = boris_tab1.receive_json()["message"]["id"]
    boris_tab2.receive_json()

    boris_tab1.send_json({"type": "read", "chat_id": chat_id, "message_id": message_id})

    expected = {"type": "read", "chat_id": chat_id, "message_id": message_id}
    assert boris_tab1.receive_json() == expected
    assert boris_tab2.receive_json() == expected


def test_rest_send_is_delivered_over_ws(
    connect: Connect, client: TestClient, ws_settings: Settings, chat_id: int
) -> None:
    boris = connect(BORIS)

    response = client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"text": "через REST"},
        headers={"Authorization": f"Bearer {token(ANYA, ws_settings)}"},
    )

    assert response.status_code == 201
    frame = boris.receive_json()
    assert frame["type"] == "message" and frame["message"] == response.json()


# --- жизненный цикл ---


def test_idle_connection_closed_with_1000(
    connect: Connect, client: TestClient, ws_settings: Settings
) -> None:
    anya = connect(ANYA)

    assert close_code(anya) == 1000


def test_extra_connection_evicts_oldest(
    connect: Connect, client: TestClient, ws_settings: Settings, users: FakeUserRepository
) -> None:
    tabs = [connect(ANYA) for _ in range(5)]

    newest = connect(ANYA)

    assert close_code(tabs[0]) == 4000
    tabs[1].send_json({"type": "ping"})
    assert tabs[1].receive_json() == {"type": "pong"}
    assert newest is not None


# --- логи и OpenAPI ---


def test_logs_contain_no_text_or_token(
    connect: Connect,
    client: TestClient,
    ws_settings: Settings,
    chat_id: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    secret_text = "секретный текст сообщения"
    anya = connect(ANYA)

    anya.send_json(send_frame(chat_id, secret_text))
    anya.receive_json()
    anya.receive_json()
    anya.send_json(send_frame(999, secret_text))  # ошибка тоже логируется без текста
    anya.receive_json()
    client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"text": secret_text},
        headers={"Authorization": f"Bearer {token(ANYA, ws_settings)}"},
    )

    assert "ws connected: user_id=1" in caplog.text
    assert secret_text not in caplog.text
    assert token(ANYA, ws_settings) not in caplog.text


def test_openapi_contains_ws_frames(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert {"ClientFrame", "ServerFrame", "SendFrame", "AckFrame", "MessageOut"} <= set(schemas)
    assert schemas["ServerFrame"]["discriminator"]["propertyName"] == "type"
