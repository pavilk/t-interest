"""Тесты chat на настоящей PostgreSQL.

Схема — из `pg_factory` (tests/conftest.py): отдельная схема на тест, настоящие миграции.
Транзакция на тест с откатом здесь не подходит: тесты гонок и outbox
коммитят в нескольких сессиях, поэтому каждому тесту — своя чистая схема.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Group, GroupStatus, Interest, Office, User, UserRole

SessionFactory = async_sessionmaker[AsyncSession]
MakeUser = Callable[[str], Awaitable[int]]


@pytest.fixture
def session_factory(pg_factory: SessionFactory) -> SessionFactory:
    return pg_factory


@pytest.fixture
async def session(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def make_user(session: AsyncSession) -> Callable[[str], Awaitable[int]]:
    """Создаёт вымышленного пользователя (и офис при первом вызове), возвращает id."""
    office: list[Office] = []

    async def make(name: str) -> int:
        if not office:
            office.append(Office(city="Екатеринбург", name="Тестовый офис"))
            session.add(office[0])
            await session.flush()
        user = User(
            email=f"{name.lower()}@example.com",
            password_hash="x",
            name=name,
            office_id=office[0].id,
            role=UserRole.USER,
            survey_completed=True,
        )
        session.add(user)
        await session.flush()
        return user.id

    return make


async def make_group(session: AsyncSession, name: str, created_by: int) -> int:
    # префикс: сиды анкеты v2 уже создают интересы с обычными названиями
    interest = Interest(name=f"Тест: {name}")
    session.add(interest)
    await session.flush()
    group = Group(
        name=name, interest_id=interest.id, created_by=created_by, status=GroupStatus.APPROVED
    )
    session.add(group)
    await session.flush()
    return group.id
