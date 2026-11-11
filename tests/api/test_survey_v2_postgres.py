from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import db
from app.core.config import get_settings
from app.main import create_app
from app.models import UserInterest
from seeds.__main__ import seed

pytestmark = pytest.mark.postgres


@pytest.fixture
async def v2_client(pg_factory, settings, monkeypatch) -> AsyncIterator[AsyncClient]:
    async with pg_factory.begin() as session:
        await seed(session)
    monkeypatch.setattr(db, "get_session_factory", lambda: pg_factory)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_all_branches_and_resubmit_are_atomic(v2_client, pg_factory):
    client = v2_client
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "branches@example.com",
            "password": "survey-v2-test-password",
            "name": "Branches",
            "office_id": 1,
        },
    )
    assert registered.status_code == 201
    headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
    survey = (await client.get("/api/v1/survey", headers=headers)).json()
    questions = {q["code"]: q for q in survey}
    options = {(q["code"], o["text"]): o for q in survey for o in q["options"]}

    def choice(code, *labels):
        return {
            "question_id": questions[code]["id"],
            "option_ids": [options[(code, label)]["id"] for label in labels],
        }

    def free(code, value):
        return {"question_id": questions[code]["id"], "text_value": value}

    full = [
        choice("v2_directions", "Спорт", "Культура и искусство", "Отдых", "Животные", "Другое"),
        choice("v2_sport", "Бег", "Другое"),
        free("v2_sport_other", "Лыжи"),
        choice("v2_art", "Музыка", "Другое"),
        free("v2_art_other", "Керамика"),
        choice("v2_rest", "Прогулки", "Другое"),
        free("v2_rest_other", "Походы"),
        free("v2_other", "Другой интерес"),
        free("v2_about", "О себе"),
    ]
    # Request order must not determine whether a condition is satisfied.
    response = await client.put("/api/v1/survey/answers", headers=headers, json=full[::-1])
    assert response.status_code == 200
    assert response.json()["survey_completed"] is True
    assert response.json()["about"] is None
    async with pg_factory() as session:
        actual = set((await session.scalars(select(UserInterest.interest_id))).all())
        assert actual == {
            options[(code, label)]["interest_id"]
            for code, label in [("v2_sport", "Бег"), ("v2_art", "Музыка"), ("v2_rest", "Прогулки")]
        }
    saved = (await client.get("/api/v1/survey/answers", headers=headers)).json()
    invalid = [choice("v2_directions", "Экология"), free("v2_sport_other", "Hidden")]
    response = await client.put("/api/v1/survey/answers", headers=headers, json=invalid)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "question_not_applicable"
    assert (await client.get("/api/v1/survey/answers", headers=headers)).json() == saved
    changed = [choice("v2_directions", "Экология", "Животные", "Настольные игры", "Технологии")]
    response = await client.put("/api/v1/survey/answers", headers=headers, json=changed)
    assert response.status_code == 200
    result = (await client.get("/api/v1/survey/answers", headers=headers)).json()
    assert len(result) == 1 and result[0]["question_id"] == questions["v2_directions"]["id"]
    async with pg_factory() as session:
        assert (await session.scalars(select(UserInterest))).all() == []
        await seed(session)
    assert (await client.get("/api/v1/survey/answers", headers=headers)).json() == result
