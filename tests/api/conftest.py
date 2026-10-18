"""API-тесты без БД: настоящие роутеры и сервисы, репозитории — фейки.

Подменяются только `get_*_repository` и `get_settings` из app/core/deps.py.
Новый модуль добавляет сюда подмену своих репозиториев.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.deps import (
    get_chat_events,
    get_chat_repository,
    get_office_repository,
    get_user_repository,
)
from app.main import create_app
from app.models import Office
from tests.fakes.repositories import (
    FakeChatEventSink,
    FakeChatRepository,
    FakeOfficeRepository,
    FakeUserRepository,
)


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def offices() -> FakeOfficeRepository:
    return FakeOfficeRepository([Office(id=1, city="Екатеринбург", name="Офис на Ленина")])


@pytest.fixture
def chats() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def chat_events() -> FakeChatEventSink:
    return FakeChatEventSink()


@pytest.fixture
async def client(
    users: FakeUserRepository,
    offices: FakeOfficeRepository,
    chats: FakeChatRepository,
    chat_events: FakeChatEventSink,
    settings: Settings,
) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_office_repository] = lambda: offices
    app.dependency_overrides[get_chat_repository] = lambda: chats
    app.dependency_overrides[get_chat_events] = lambda: chat_events
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
