"""API-тесты без БД: настоящие роутеры и сервисы, репозитории — фейки.

Подменяются только `get_*_repository` и `get_settings` из app/core/deps.py.
Новый модуль добавляет сюда подмену своих репозиториев.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.deps import get_office_repository, get_user_repository
from app.main import create_app
from app.models import Office
from tests.fakes.repositories import FakeOfficeRepository, FakeUserRepository


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def offices() -> FakeOfficeRepository:
    return FakeOfficeRepository([Office(id=1, city="Екатеринбург", name="Офис на Ленина")])


@pytest.fixture
async def client(
    users: FakeUserRepository, offices: FakeOfficeRepository, settings: Settings
) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_office_repository] = lambda: offices
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
