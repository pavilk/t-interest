"""Сессия БД, единственное место commit и outbox событий.

Outbox: сервис кладёт события в `get_outbox(session)`, а `session_scope` отдаёт их
в `EventPublisher` сразу после commit. При rollback события выбрасываются, поэтому
клиенты никогда не видят то, чего нет в БД. Хуки SQLAlchemy `after_commit` не подходят:
они срабатывают и на выходе из savepoint.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Protocol

from fastapi.requests import HTTPConnection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_OUTBOX_KEY = "outbox"


class EventPublisher(Protocol):
    """Доставщик событий. Синхронный и неблокирующий: только раскладывает по очередям."""

    def publish(self, event: Any) -> None: ...


class Outbox:
    """События одной сессии, ждущие commit."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def add(self, event: Any) -> None:
        self.events.append(event)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    # движок создаётся при первом запросе, а не при импорте: тестам без БД он не нужен
    engine = create_async_engine(get_settings().database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def get_outbox(session: AsyncSession) -> Outbox:
    """Outbox, привязанный к сессии; создаётся один раз на сессию."""
    outbox = session.info.get(_OUTBOX_KEY)
    if outbox is None:
        outbox = session.info[_OUTBOX_KEY] = Outbox()
    assert isinstance(outbox, Outbox)
    return outbox


@asynccontextmanager
async def session_scope(publisher: EventPublisher | None = None) -> AsyncIterator[AsyncSession]:
    """Unit of work. Единственное место, где вызывается commit.

    Используется get_session (HTTP, сессия на запрос) и WebSocket (сессия на каждый фрейм).
    Репозитории и сервисы не коммитят: они только добавляют, меняют и читают объекты.
    """
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # после rollback и при отмене события просто выбрасываются вместе с сессией
            outbox = session.info.pop(_OUTBOX_KEY, None)
        if publisher is not None and isinstance(outbox, Outbox):
            _publish(publisher, outbox)


def _publish(publisher: EventPublisher, outbox: Outbox) -> None:
    for item in outbox.events:
        try:
            publisher.publish(item)
        except Exception:
            # commit уже случился: ошибка доставки не должна его ломать; payload не логируем
            logger.exception("event publish failed: %s", type(item).__name__)


def get_event_publisher(conn: HTTPConnection) -> EventPublisher | None:
    publisher: EventPublisher | None = getattr(conn.app.state, "event_publisher", None)
    return publisher


async def get_session(conn: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """Unit of work на один HTTP-запрос.

    Подключается только через `SessionDep` из app/core/deps.py (scope="function"),
    чтобы commit произошёл до отправки ответа клиенту.
    """
    async with session_scope(get_event_publisher(conn)) as session:
        yield session
