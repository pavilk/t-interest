"""Тесты репозиториев на настоящей PostgreSQL.

Нужна переменная TEST_DATABASE_URL с отдельной базой: схема в ней пересоздаётся
через Base.metadata.create_all, данные очищаются перед каждым тестом.
Без переменной тесты пропускаются (skipped), а не падают.

Транзакция на тест с откатом здесь не подходит: тесты гонок и outbox
коммитят в нескольких сессиях. Поэтому перед каждым тестом — TRUNCATE.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base, Group, GroupStatus, Interest, Office, User, UserRole

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

SessionFactory = async_sessionmaker[AsyncSession]
MakeUser = Callable[[str], Awaitable[int]]


@pytest.fixture(scope="session")
def _schema() -> None:
    assert TEST_DATABASE_URL is not None

    async def recreate() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(recreate())


@pytest.fixture
async def session_factory(_schema: None) -> AsyncIterator[SessionFactory]:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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
    interest = Interest(name=name)
    session.add(interest)
    await session.flush()
    group = Group(
        name=name, interest_id=interest.id, created_by=created_by, status=GroupStatus.APPROVED
    )
    session.add(group)
    await session.flush()
    return group.id
