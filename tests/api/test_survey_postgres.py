from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import db
from app.core.config import Settings, get_settings
from app.core.security import create_token
from app.main import create_app
from app.models import User, UserAnswer, UserInterest
from seeds.__main__ import seed

pytestmark = pytest.mark.postgres


@pytest.fixture
async def pg_client(
    pg_data: async_sessionmaker[AsyncSession], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_data)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


def headers(settings: Settings, user_id: int = 1) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(user_id, 'access', settings)}"}


ANSWERS = [{"question_id": 1, "option_ids": [1]}, {"question_id": 2, "option_ids": [3]}]


@pytest.mark.parametrize("completed", [False, True])
async def test_nul_returns_422_and_preserves_saved_state(
    pg_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
    completed: bool,
) -> None:
    auth = headers(settings)
    if completed:
        response = await pg_client.put(
            "/api/v1/survey/answers",
            headers=auth,
            json=ANSWERS + [{"question_id": 3, "text_value": "previous text"}],
        )
        assert response.status_code == 200
    async with pg_data() as session:
        previous_answers = list(
            (
                await session.execute(
                    select(
                        UserAnswer.id,
                        UserAnswer.question_id,
                        UserAnswer.option_id,
                        UserAnswer.text_value,
                    ).order_by(UserAnswer.id)
                )
            ).all()
        )
        previous_interests = list(
            (
                await session.execute(
                    select(UserInterest.user_id, UserInterest.interest_id).order_by(
                        UserInterest.user_id, UserInterest.interest_id
                    )
                )
            ).all()
        )

    response = await pg_client.put(
        "/api/v1/survey/answers",
        headers=auth,
        json=[
            {"question_id": 1, "option_ids": [2]},
            {"question_id": 2, "option_ids": [4]},
            {"question_id": 3, "text_value": "review\x00marker"},
        ],
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["message"], str)
    assert "review" not in response.text and "marker" not in response.text
    async with pg_data() as session:
        assert (
            list(
                (
                    await session.execute(
                        select(
                            UserAnswer.id,
                            UserAnswer.question_id,
                            UserAnswer.option_id,
                            UserAnswer.text_value,
                        ).order_by(UserAnswer.id)
                    )
                ).all()
            )
            == previous_answers
        )
        assert (
            list(
                (
                    await session.execute(
                        select(UserInterest.user_id, UserInterest.interest_id).order_by(
                            UserInterest.user_id, UserInterest.interest_id
                        )
                    )
                ).all()
            )
            == previous_interests
        )
        user = await session.get(User, 1)
        assert user is not None and user.survey_completed is completed


async def test_real_commit_resubmit_and_invalid_request_rollback(
    pg_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    auth = headers(settings)
    assert (await pg_client.get("/api/v1/survey", headers=auth)).status_code == 200
    response = await pg_client.put("/api/v1/survey/answers", headers=auth, json=ANSWERS)
    assert response.status_code == 200
    assert response.json()["survey_completed"] is True
    response = await pg_client.put("/api/v1/survey/answers", headers=auth, json=ANSWERS[:1])
    assert response.status_code == 422
    async with pg_data() as session:
        assert len((await session.scalars(select(UserAnswer))).all()) == 2
        assert [
            (r.user_id, r.interest_id) for r in (await session.scalars(select(UserInterest))).all()
        ] == [(1, 11)]
    changed = [{"question_id": 1, "option_ids": [2]}, {"question_id": 2, "option_ids": [4]}]
    assert (
        await pg_client.put("/api/v1/survey/answers", headers=auth, json=changed)
    ).status_code == 200
    result = (await pg_client.get("/api/v1/survey/answers", headers=auth)).json()
    assert [row["option_ids"] for row in result] == [[2], [4]]
    assert (
        await pg_client.get("/api/v1/survey/answers", headers=headers(settings, 2))
    ).json() == []


async def test_error_after_answers_rolls_back_all_writes(
    pg_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    # A real DB failure after answer insertion, before completion.
    async with pg_data.begin() as session:
        await session.execute(
            text("ALTER TABLE user_interests ADD CONSTRAINT test_failure CHECK (false)")
        )
    response = await pg_client.put(
        "/api/v1/survey/answers", headers=headers(settings), json=ANSWERS
    )
    assert response.status_code == 500
    async with pg_data() as session:
        assert (await session.scalars(select(UserAnswer))).all() == []
        assert (await session.scalars(select(UserInterest))).all() == []
        user = await session.get(User, 1)
        assert user is not None and not user.survey_completed


async def test_commit_failure_never_returns_success(
    pg_client: AsyncClient,
    pg_data: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    # Deferred constraint: INSERT and flush succeed; the transaction fails only at COMMIT.
    async with pg_data.begin() as session:
        await session.execute(
            text("""
            CREATE FUNCTION test_fail_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test commit failure'; RETURN NEW; END $$
        """)
        )
        await session.execute(
            text("""
            CREATE CONSTRAINT TRIGGER test_fail_commit AFTER INSERT ON user_answers
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION test_fail_commit()
        """)
        )
    response = await pg_client.put(
        "/api/v1/survey/answers", headers=headers(settings), json=ANSWERS
    )
    assert response.status_code == 500
    async with pg_data() as session:
        assert (await session.scalars(select(UserAnswer))).all() == []
        assert (await session.scalars(select(UserInterest))).all() == []
        user = await session.get(User, 1)
        assert user is not None and not user.survey_completed


async def test_registration_to_seeded_survey_and_repeated_seed(
    pg_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with pg_factory.begin() as session:
        await seed(session)
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_factory)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "demo@example.com",
                "password": "test-survey-password",
                "name": "Демо",
                "office_id": 1,
            },
        )
        assert response.status_code == 201
        auth = {"Authorization": "Bearer " + response.json()["access_token"]}
        assert not (await client.get("/api/v1/me", headers=auth)).json()["survey_completed"]
        survey = (await client.get("/api/v1/survey", headers=auth)).json()
        answers = [
            {"question_id": q["id"], "option_ids": [q["options"][0]["id"]]}
            for q in survey
            if q["type"] != "text"
        ]
        response = await client.put("/api/v1/survey/answers", headers=auth, json=answers)
        assert response.status_code == 200 and response.json()["survey_completed"] is True
        before = (await client.get("/api/v1/survey/answers", headers=auth)).json()
        async with pg_factory.begin() as session:
            await seed(session)
        assert (await client.get("/api/v1/survey/answers", headers=auth)).json() == before
