"""Реестр WS-соединений в памяти процесса и доставка событий чата.

Работает только с одним воркером (см. CLAUDE.md). У каждого соединения своя
ограниченная очередь и единственный писатель: медленный клиент не тормозит
остальных, конкурентной записи в сокет нет.
"""

import asyncio
import logging

from fastapi import WebSocket
from pydantic import BaseModel

from app.schemas.ws import ChatCreatedFrame, ChatRemovedFrame, MessageFrame, ReadSyncFrame
from app.services.notifier import ChatCreated, ChatEvent, ChatRemoved, MessageCreated, ReadUpdated

logger = logging.getLogger(__name__)

CLOSE_NORMAL = 1000
CLOSE_TOO_BIG = 1009
CLOSE_TRY_AGAIN_LATER = 1013  # очередь переполнена: клиент переподключается и догружает after_id
CLOSE_EVICTED = 4000  # открыто слишком много соединений, закрыто самое старое
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403


class Connection:
    """Одно WS-соединение. В сокет пишет только `run_writer`."""

    def __init__(self, websocket: WebSocket, user_id: int, queue_size: int) -> None:
        self.websocket = websocket
        self.user_id = user_id
        self.close_code: int | None = None
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=queue_size)

    def offer(self, text: str) -> bool:
        """Кладёт готовый JSON в очередь; False, если соединение закрывается или переполнено."""
        if self.close_code is not None:
            return False
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            return False
        return True

    def request_close(self, code: int) -> None:
        """Писатель закроет сокет после того, как увидит метку. Повторный вызов — no-op."""
        if self.close_code is not None:
            return
        self.close_code = code
        while not self._queue.empty():  # недоставленное выбрасываем: клиент догрузит по after_id
            self._queue.get_nowait()
        self._queue.put_nowait(None)

    async def run_writer(self) -> None:
        try:
            while (text := await self._queue.get()) is not None:
                await self.websocket.send_text(text)
            await self.websocket.close(code=self.close_code or CLOSE_NORMAL)
        except Exception:  # клиент уже отключился: читатель увидит disconnect сам
            logger.debug("ws writer stopped: user_id=%s", self.user_id)


class ConnectionManager:
    """Реализует Notifier: превращает события чата во фреймы и раскладывает по соединениям."""

    def __init__(self) -> None:
        self._by_user: dict[int, list[Connection]] = {}

    def register(self, conn: Connection, max_per_user: int) -> None:
        connections = self._by_user.setdefault(conn.user_id, [])
        connections.append(conn)
        while len(connections) > max_per_user:
            oldest = connections.pop(0)
            oldest.request_close(CLOSE_EVICTED)

    def unregister(self, conn: Connection) -> None:
        connections = self._by_user.get(conn.user_id)
        if connections is None or conn not in connections:
            return
        connections.remove(conn)
        if not connections:
            del self._by_user[conn.user_id]

    def send_to(self, conn: Connection, frame: BaseModel) -> None:
        """Ответ на собственный фрейм соединения (auth_ok, ack, error, pong)."""
        self._deliver(conn, frame.model_dump_json())

    def publish(self, event: ChatEvent) -> None:
        frame: BaseModel
        match event:
            case MessageCreated(message=message, recipient_ids=recipients):
                frame = MessageFrame(message=message)
            case ReadUpdated(user_id=user_id, chat_id=chat_id, message_id=message_id):
                frame = ReadSyncFrame(chat_id=chat_id, message_id=message_id)
                recipients = (user_id,)
            case ChatCreated(chat_id=chat_id, user_ids=recipients):
                frame = ChatCreatedFrame(chat_id=chat_id)
            case ChatRemoved(chat_id=chat_id, user_ids=recipients):
                frame = ChatRemovedFrame(chat_id=chat_id)
        text = frame.model_dump_json()  # один раз на событие, а не на соединение
        for user_id in recipients:
            for conn in list(self._by_user.get(user_id, ())):
                self._deliver(conn, text)

    def connections_of(self, user_id: int) -> list[Connection]:
        return list(self._by_user.get(user_id, ()))

    def _deliver(self, conn: Connection, text: str) -> None:
        if not conn.offer(text) and conn.close_code is None:
            logger.info("ws send queue overflow: user_id=%s", conn.user_id)
            conn.request_close(CLOSE_TRY_AGAIN_LATER)
            self.unregister(conn)
