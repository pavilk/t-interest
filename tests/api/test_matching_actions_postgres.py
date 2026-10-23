import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import db
from app.core.config import Settings, get_settings
from app.core.security import create_token
from app.main import create_app
from app.models import Chat, Match, MatchStatus, User
from app.repositories.match import SqlMatchRepository
from app.services.matching import MatchingService

pytestmark = pytest.mark.postgres
RECOMMENDATIONS = "/api/v1/recommendations/people"


@pytest.fixture
async def action_client(
    pg_data: async_sessionmaker[AsyncSession], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async with pg_data.begin() as session:
        for user in await session.scalars(select(User)):
            user.survey_completed = True
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_data)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


def headers(settings: Settings, user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(user_id, 'access', settings)}"}


@pytest.mark.parametrize("action", ["like", "skip"])
@pytest.mark.parametrize(
    ("target", "status", "code"),
    [
        (2147483647, 404, "user_not_found"),
        (2147483648, 422, "validation_error"),
        (9223372036854775808, 422, "validation_error"),
    ],
)
async def test_target_id_range_returns_domain_or_validation_error(
    action_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
    action: str,
    target: int,
    status: int,
    code: str,
) -> None:
    response = await action_client.post(
        f"/api/v1/matches/{target}/{action}", headers=headers(settings, 1)
    )
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    async with pg_data() as session:
        assert await session.scalar(select(func.count()).select_from(Match)) == 0


async def test_skip_then_like_persists_pair_and_excludes_both_users(
    action_client: AsyncClient, pg_data: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    first, second = headers(settings, 1), headers(settings, 2)
    response = await action_client.post("/api/v1/matches/2/skip", headers=first)
    assert response.status_code == 204 and response.content == b""
    assert (await action_client.get(RECOMMENDATIONS, headers=first)).json() == []
    assert [
        r["user"]["id"] for r in (await action_client.get(RECOMMENDATIONS, headers=second)).json()
    ] == [1]
    async with pg_data() as session:
        pair = (await session.scalars(select(Match))).one()
        original_id, original_created_at = pair.id, pair.created_at
        assert (pair.a_status, pair.b_status, pair.score) == (
            MatchStatus.DECLINED,
            MatchStatus.PENDING,
            0.0,
        )
    response = await action_client.post("/api/v1/matches/1/like", headers=second)
    assert response.status_code == 200
    assert response.json() == {"matched": True, "chat_id": None}
    for auth, target in [(first, 2), (second, 1)]:
        response = await action_client.post(f"/api/v1/matches/{target}/like", headers=auth)
        assert response.status_code == 200
        response = await action_client.post(f"/api/v1/matches/{target}/skip", headers=auth)
        assert response.status_code == 204
        assert (await action_client.get(RECOMMENDATIONS, headers=auth)).json() == []
    async with pg_data() as session:
        pair = (await session.scalars(select(Match))).one()
        assert (pair.id, pair.created_at) == (original_id, original_created_at)
        assert (pair.a_status, pair.b_status, pair.score) == (
            MatchStatus.DECLINED,
            MatchStatus.ACCEPTED,
            0.15,
        )
        assert await session.scalar(select(func.count()).select_from(Chat)) == 0


@pytest.mark.parametrize(
    ("first_actor", "first_action", "second_actor", "second_action"),
    [
        (1, "like", 1, "like"),
        (1, "like", 2, "like"),
        (1, "like", 1, "skip"),
        (1, "like", 2, "skip"),
        (1, "skip", 2, "skip"),
    ],
)
@pytest.mark.parametrize("existing_skip", [False, True])
async def test_concurrent_actions_serialize_one_pair(
    action_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    first_actor: int,
    first_action: str,
    second_actor: int,
    second_action: str,
    existing_skip: bool,
) -> None:
    if existing_skip:
        response = await action_client.post("/api/v1/matches/2/skip", headers=headers(settings, 1))
        assert response.status_code == 204
    ready = asyncio.Barrier(2)
    original = SqlMatchRepository.get_or_create_for_update

    async def start_together(self: SqlMatchRepository, user_a: int, user_b: int) -> Match:
        await asyncio.wait_for(ready.wait(), timeout=5)
        return await original(self, user_a, user_b)

    monkeypatch.setattr(SqlMatchRepository, "get_or_create_for_update", start_together)
    results = await asyncio.wait_for(
        asyncio.gather(
            *[
                action_client.post(
                    f"/api/v1/matches/{3 - actor}/{action}", headers=headers(settings, actor)
                )
                for actor, action in [(first_actor, first_action), (second_actor, second_action)]
            ]
        ),
        timeout=15,
    )
    for response, action in zip(results, [first_action, second_action], strict=True):
        assert response.status_code == (200 if action == "like" else 204), response.text
        if action == "like":
            assert response.json() == {"matched": True, "chat_id": None}
    async with pg_data() as session:
        pair = (await session.scalars(select(Match))).one()
        assert (pair.user_a_id, pair.user_b_id) == (1, 2)
        if first_action == second_action == "skip":
            assert (pair.a_status, pair.b_status, pair.score) == (
                MatchStatus.DECLINED,
                MatchStatus.DECLINED,
                0.0,
            )
        else:
            assert MatchStatus.ACCEPTED in (pair.a_status, pair.b_status)
            assert pair.score == 0.15
            if second_action == "like":
                assert (pair.a_status, pair.b_status).count(MatchStatus.ACCEPTED) == 1
        assert await session.scalar(select(func.count()).select_from(Chat)) == 0


@pytest.mark.parametrize("failure", ["integration", "commit"])
@pytest.mark.parametrize("existing_skip", [False, True])
async def test_failed_like_rolls_back_pair_changes(
    action_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    existing_skip: bool,
) -> None:
    if existing_skip:
        response = await action_client.post("/api/v1/matches/2/skip", headers=headers(settings, 1))
        assert response.status_code == 204

    async def fail_integration(self: MatchingService, user_a: int, user_b: int) -> int | None:
        raise RuntimeError("integration failure")

    async def fail_commit(self: AsyncSession) -> None:
        await self.flush()
        raise RuntimeError("commit failure")

    if failure == "integration":
        monkeypatch.setattr(MatchingService, "_ensure_direct_chat", fail_integration)
    else:
        monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    response = await action_client.post("/api/v1/matches/2/like", headers=headers(settings, 1))
    assert response.status_code == 500
    async with pg_data() as session:
        pairs = list(await session.scalars(select(Match)))
        if existing_skip:
            (pair,) = pairs
            assert (pair.a_status, pair.b_status, pair.score) == (
                MatchStatus.DECLINED,
                MatchStatus.PENDING,
                0.0,
            )
        else:
            assert pairs == []
