"""Outbox в session_scope: события уходят только после реального commit."""

from typing import Any

import pytest
from sqlalchemy import text

import app.core.db as db
from app.core.db import get_outbox, session_scope
from tests.repositories.conftest import TEST_DATABASE_URL, SessionFactory

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[Any] = []
        self.fail = fail

    def publish(self, event: Any) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("delivery failed")


@pytest.fixture(autouse=True)
def _use_test_db(monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory) -> None:
    monkeypatch.setattr(db, "get_session_factory", lambda: session_factory)


async def test_events_published_only_after_commit() -> None:
    recorder = Recorder()

    async with session_scope(recorder) as session:
        await session.execute(text("SELECT 1"))
        get_outbox(session).add("event")
        assert recorder.events == []

    assert recorder.events == ["event"]


async def test_rollback_discards_events() -> None:
    recorder = Recorder()

    with pytest.raises(RuntimeError):
        async with session_scope(recorder) as session:
            await session.execute(text("SELECT 1"))
            get_outbox(session).add("phantom")
            raise RuntimeError("boom")

    assert recorder.events == []


async def test_savepoint_does_not_publish_or_discard() -> None:
    recorder = Recorder()

    async with session_scope(recorder) as session:
        get_outbox(session).add("outer")
        async with session.begin_nested():
            await session.execute(text("SELECT 1"))
        assert recorder.events == []  # release savepoint — не commit
        try:
            async with session.begin_nested():
                await session.execute(text("SELECT 1"))
                raise ValueError
        except ValueError:
            pass

    assert recorder.events == ["outer"]


async def test_publish_error_does_not_break_commit(session_factory: SessionFactory) -> None:
    recorder = Recorder(fail=True)

    async with session_scope(recorder) as session:
        await session.execute(text("CREATE TEMP TABLE IF NOT EXISTS t (x int)"))
        await session.execute(text("INSERT INTO offices (city, name) VALUES ('Город', 'Офис')"))
        get_outbox(session).add("first")
        get_outbox(session).add("second")

    assert recorder.events == ["first", "second"]
    async with session_factory() as check:
        assert await check.scalar(text("SELECT count(*) FROM offices")) == 1
