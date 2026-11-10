"""API-тесты без БД: настоящие роутеры и сервисы, репозитории — фейки.

Подменяются только `get_*_repository` и `get_settings` из app/core/deps.py.
Новый модуль добавляет сюда подмену своих репозиториев.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.deps import (
    get_interest_repository,
    get_match_repository,
    get_office_repository,
    get_survey_repository,
    get_user_repository,
)
from app.main import create_app
from app.models import Office
from tests.fakes.repositories import (
    FakeInterestRepository,
    FakeMatchRepository,
    FakeOfficeRepository,
    FakeSurveyRepository,
    FakeUserRepository,
)


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def offices() -> FakeOfficeRepository:
    return FakeOfficeRepository([Office(id=1, city="Екатеринбург", name="Офис на Ленина")])


@pytest.fixture
def surveys(users: FakeUserRepository, interests: FakeInterestRepository) -> FakeSurveyRepository:
    return FakeSurveyRepository(users=users, interests=interests)


@pytest.fixture
def matches() -> FakeMatchRepository:
    return FakeMatchRepository()


@pytest.fixture
def interests() -> FakeInterestRepository:
    return FakeInterestRepository()


@pytest.fixture
async def client(
    users: FakeUserRepository,
    offices: FakeOfficeRepository,
    settings: Settings,
    surveys: FakeSurveyRepository,
    interests: FakeInterestRepository,
    matches: FakeMatchRepository,
) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_office_repository] = lambda: offices
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_survey_repository] = lambda: surveys
    app.dependency_overrides[get_interest_repository] = lambda: interests
    app.dependency_overrides[get_match_repository] = lambda: matches
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
