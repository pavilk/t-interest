import asyncio
import json
from typing import Any, cast

from fastapi import WebSocket

from app.api.connections import Connection, ConnectionManager
from app.schemas.chat import MessageOut, UserBrief
from app.schemas.ws import PongFrame
from app.services.notifier import ChatCreated, MessageCreated, ReadUpdated


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int) -> None:
        self.closed_with = code


def connect(
    manager: ConnectionManager, user_id: int, queue_size: int = 10, max_per_user: int = 5
) -> tuple[Connection, FakeSocket]:
    socket = FakeSocket()
    conn = Connection(cast(WebSocket, socket), user_id, queue_size)
    manager.register(conn, max_per_user)
    return conn, socket


async def drain(*conns: Connection) -> None:
    """Запускает писателей, даёт им отправить очередь и закрывает соединения."""
    writers = [asyncio.create_task(conn.run_writer()) for conn in conns]
    await asyncio.sleep(0.01)
    for conn in conns:
        conn.request_close(1000)
    await asyncio.gather(*writers)


def message(chat_id: int = 1) -> MessageOut:
    return MessageOut(
        id=1,
        chat_id=chat_id,
        sender=UserBrief(id=1, name="Аня"),
        text="привет",
        created_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        client_msg_id=None,
    )


async def test_event_reaches_all_connections_of_recipients_only() -> None:
    manager = ConnectionManager()
    anya_tab1, s1 = connect(manager, 1)
    anya_tab2, s2 = connect(manager, 1)
    boris, s3 = connect(manager, 2)
    vera, s4 = connect(manager, 3)

    manager.publish(MessageCreated(message=message(), recipient_ids=(1, 2)))
    await drain(anya_tab1, anya_tab2, boris, vera)

    assert [f["type"] for f in s1.sent] == ["message"]
    assert s2.sent == s1.sent == s3.sent
    assert s4.sent == []


async def test_read_goes_to_reader_tabs_and_chat_created_to_new_members() -> None:
    manager = ConnectionManager()
    anya, s1 = connect(manager, 1)
    boris, s2 = connect(manager, 2)

    manager.publish(ReadUpdated(user_id=1, chat_id=7, message_id=5))
    manager.publish(ChatCreated(chat_id=8, user_ids=(2,)))
    await drain(anya, boris)

    assert s1.sent == [{"type": "read", "chat_id": 7, "message_id": 5}]
    assert s2.sent == [{"type": "chat_created", "chat_id": 8}]


async def test_frame_serialized_once_per_event(monkeypatch: Any) -> None:
    manager = ConnectionManager()
    conns = [connect(manager, 1)[0], connect(manager, 2)[0]]
    calls = 0
    original = MessageOut.model_dump_json

    def counting(self: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return original(self, **kwargs)

    from app.schemas import ws

    monkeypatch.setattr(ws.MessageFrame, "model_dump_json", counting)
    manager.publish(MessageCreated(message=message(), recipient_ids=(1, 2)))
    await drain(*conns)

    assert calls == 1


async def test_overflow_closes_only_slow_connection() -> None:
    manager = ConnectionManager()
    slow, slow_socket = connect(manager, 1, queue_size=1)
    fast, fast_socket = connect(manager, 2, queue_size=10)

    for _ in range(2):  # писатель не запущен: у медленного вторая не влезает
        manager.publish(MessageCreated(message=message(), recipient_ids=(1, 2)))
    await drain(slow, fast)

    assert slow_socket.closed_with == 1013 and slow_socket.sent == []
    assert fast_socket.closed_with == 1000 and len(fast_socket.sent) == 2
    assert manager.connections_of(1) == []


async def test_sixth_connection_evicts_oldest() -> None:
    manager = ConnectionManager()
    conns = [connect(manager, 1)[0] for _ in range(6)]

    assert conns[0].close_code == 4000
    assert manager.connections_of(1) == conns[1:]


async def test_unregister_is_idempotent_and_send_to_targets_one_connection() -> None:
    manager = ConnectionManager()
    tab1, s1 = connect(manager, 1)
    tab2, s2 = connect(manager, 1)

    manager.send_to(tab1, PongFrame())
    manager.unregister(tab2)
    manager.unregister(tab2)
    await drain(tab1, tab2)

    assert s1.sent == [{"type": "pong"}] and s2.sent == []
    assert manager.connections_of(1) == [tab1]


async def test_writer_survives_disconnected_client() -> None:
    class BrokenSocket(FakeSocket):
        async def send_text(self, text: str) -> None:
            raise RuntimeError("gone")

    conn = Connection(cast(WebSocket, BrokenSocket()), 1, 10)
    conn.offer("{}")

    await asyncio.wait_for(conn.run_writer(), 1)  # не бросает и не зависает
