"""События чата и протоколы их доставки.

Сервис кладёт события в `ChatEventSink` (outbox сессии), а `Notifier`
(менеджер WS-соединений) получает их только после commit.
"""

from dataclasses import dataclass
from typing import Protocol

from app.schemas.chat import MessageOut


@dataclass(frozen=True)
class MessageCreated:
    message: MessageOut
    recipient_ids: tuple[int, ...]


@dataclass(frozen=True)
class ReadUpdated:
    user_id: int
    chat_id: int
    message_id: int


@dataclass(frozen=True)
class ChatCreated:
    chat_id: int
    user_ids: tuple[int, ...]


@dataclass(frozen=True)
class ChatRemoved:
    chat_id: int
    user_ids: tuple[int, ...]


ChatEvent = MessageCreated | ReadUpdated | ChatCreated | ChatRemoved


class ChatEventSink(Protocol):
    def add(self, event: ChatEvent) -> None: ...


class Notifier(Protocol):
    def publish(self, event: ChatEvent) -> None:
        """Синхронно, не блокирует и не бросает исключений."""
        ...
