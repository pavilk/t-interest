from collections.abc import Sequence

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import db
from app.core.config import Settings, get_settings
from app.core.security import create_token
from app.main import create_app
from app.models import Match, MatchStatus, Office, User, UserInterest
from app.schemas.user import PublicIdentityOut
from app.services.identity import IdentityService
from seeds.__main__ import seed

pytestmark = pytest.mark.postgres
PATH = "/api/v1/recommendations/people"


@pytest.mark.parametrize("change", ["interests", "office"])
async def test_concurrent_commit_keeps_score_and_public_profile_consistent(
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    async with pg_data.begin() as session:
        session.add(Office(id=2, city="Тестовый город", name="Второй офис"))
        for user in (await session.scalars(select(User))).all():
            user.survey_completed = True
        session.add_all(
            [UserInterest(user_id=1, interest_id=11), UserInterest(user_id=2, interest_id=11)]
        )
    original = IdentityService.get_public_profiles

    async def commit_update_before_public_read(
        self: IdentityService,
        user_ids: Sequence[int],
    ) -> dict[int, PublicIdentityOut]:
        # Commit a real update on another connection between recommendation reads.
        async with pg_data.begin() as session:
            if change == "interests":
                await session.execute(delete(UserInterest).where(UserInterest.user_id == 2))
                session.add(UserInterest(user_id=2, interest_id=12))
            else:
                user = await session.get(User, 2)
                assert user is not None
                user.office_id = 2
        return await original(self, user_ids)

    monkeypatch.setattr(IdentityService, "get_public_profiles", commit_update_before_public_read)
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_data)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            PATH, headers={"Authorization": f"Bearer {create_token(1, 'access', settings)}"}
        )
    assert response.status_code == 200
    (row,) = response.json()
    assert row["user"]["id"] == 2
    assert row["score"] == (0.15 if change == "interests" else 1.05)
    assert row["same_office"] is (change == "interests")
    assert row["user"]["office"]["id"] == (1 if change == "interests" else 2)
    assert [i["id"] for i in row["common_interests"]] == ([] if change == "interests" else [11])
    assert [i["id"] for i in row["user"]["interests"]] == ([12] if change == "interests" else [11])


async def test_onboarding_recommendations_profile_changes_and_pair(
    pg_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with pg_factory.begin() as session:
        await seed(session)
        office = (await session.scalars(select(Office))).one()
        first_office_id = office.id
        second_office = Office(city=office.city, name="Другой тестовый офис")
        session.add(second_office)
        await session.flush()
        second_office_id = second_office.id
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_factory)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        auth = []
        ids = []
        for n in range(2):
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"matching{n}@example.com",
                    "password": "fictional-password",
                    "name": f"Персона {n}",
                    "office_id": first_office_id,
                },
            )
            assert response.status_code == 201
            headers = {"Authorization": "Bearer " + response.json()["access_token"]}
            auth.append(headers)
            ids.append((await client.get("/api/v1/me", headers=headers)).json()["id"])
        survey = (await client.get("/api/v1/survey", headers=auth[0])).json()
        answers = [
            {"question_id": q["id"], "option_ids": [q["options"][0]["id"]]}
            for q in survey
            if q["type"] != "text"
        ]
        text_question = next(q for q in survey if q["type"] == "text")
        for headers in auth:
            response = await client.put(
                "/api/v1/survey/answers",
                headers=headers,
                json=answers
                + [{"question_id": text_question["id"], "text_value": "Рисую акварелью"}],
            )
            assert response.status_code == 200
        response = await client.get(PATH, headers=auth[0])
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1 and result[0]["user"]["id"] == ids[1]
        assert result[0]["score"] == 1.15 and result[0]["same_office"] is True
        assert result[0]["common_interests"][0]["name"] == "Бег"
        assert result[0]["user"]["free_answers"] == [
            {
                "question_id": text_question["id"],
                "question_text": text_question["text"],
                "text_value": "Рисую акварелью",
            }
        ]
        response = await client.patch(
            "/api/v1/me",
            headers=auth[1],
            json={
                "office_id": second_office_id,
            },
        )
        assert response.status_code == 200
        result = (await client.get(PATH, headers=auth[0])).json()
        assert result[0]["score"] == 1.05 and result[0]["same_office"] is False
        # Changing the selected leaf removes the candidate: city alone does not qualify.
        multi = next(q for q in survey if q["type"] == "multi")
        changed = [
            a
            if a["question_id"] != multi["id"]
            else {"question_id": multi["id"], "option_ids": [multi["options"][1]["id"]]}
            for a in answers
        ]
        response = await client.put("/api/v1/survey/answers", headers=auth[1], json=changed)
        assert response.status_code == 200
        assert (await client.get(PATH, headers=auth[0])).json() == []
        response = await client.patch(
            "/api/v1/me",
            headers=auth[1],
            json={
                "office_id": first_office_id,
            },
        )
        assert response.status_code == 200
        result = (await client.get(PATH, headers=auth[0])).json()
        assert result[0]["score"] == 0.15
        assert result[0]["common_interests"] == []
        assert result[0]["user"]["free_answers"] == []
        async with pg_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Match)) == 0
        response = await client.post(f"/api/v1/matches/{ids[1]}/like", headers=auth[0])
        assert response.status_code == 200
        assert response.json() == {"matched": True, "chat_id": None}
        for headers in auth:
            assert (await client.get(PATH, headers=headers)).json() == []
    async with pg_factory() as session:
        pair = (await session.scalars(select(Match))).one()
        assert (pair.user_a_id, pair.user_b_id) == (ids[0], ids[1])
        assert (pair.a_status, pair.b_status, pair.score) == (
            MatchStatus.ACCEPTED,
            MatchStatus.PENDING,
            0.15,
        )


async def test_query_count_does_not_grow_per_recommended_person(
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with pg_data.begin() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.survey_completed = True
        session.add_all(
            [
                User(
                    id=i,
                    email=f"batch{i}@example.com",
                    password_hash="unused",
                    name="Коллега",
                    office_id=1,
                    survey_completed=True,
                )
                for i in range(3, 23)
            ]
        )
        engine = session.bind
        assert engine is not None
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_data)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {create_token(1, 'access', settings)}"}
            small = await client.get(PATH, headers=headers, params={"limit": 1})
            assert small.status_code == 200 and len(small.json()) == 1
            small_count = len(statements)
            statements.clear()
            large = await client.get(PATH, headers=headers, params={"limit": 20})
            assert large.status_code == 200 and len(large.json()) == 20
            assert len(statements) == small_count
            assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
