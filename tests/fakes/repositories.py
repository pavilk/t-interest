"""Фейковые репозитории в памяти для unit-тестов сервисов.

Каждый фейк реализует тот же Protocol из app/repositories/interfaces.py,
что и настоящий Sql*Repository. Если добавляешь метод в Protocol — добавь его и сюда.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models import (
    AnswerOption,
    Interest,
    Match,
    MatchStatus,
    Office,
    Question,
    QuestionType,
    User,
    UserAnswer,
)


class FakeUserRepository:
    def __init__(self) -> None:
        self.items: dict[int, User] = {}
        self._next_id = 1

    async def get_by_id(self, user_id: int) -> User | None:
        return self.items.get(user_id)

    async def get_by_id_for_update(self, user_id: int) -> User | None:
        return self.items.get(user_id)

    async def list_by_ids(self, user_ids: Sequence[int]) -> Sequence[User]:
        return [u for u in self.items.values() if u.id in user_ids]

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.items.values() if u.email == email), None)

    async def add(self, user: User) -> User:
        user.id = self._next_id  # как flush в БД: объект получает id
        self._next_id += 1
        self.items[user.id] = user
        return user


class FakeOfficeRepository:
    def __init__(self, offices: Sequence[Office] = ()) -> None:
        self.items: dict[int, Office] = {o.id: o for o in offices}

    async def get_by_id(self, office_id: int) -> Office | None:
        return self.items.get(office_id)

    async def list_all(self) -> Sequence[Office]:
        return sorted(self.items.values(), key=lambda o: (o.city, o.name))


class FakeSurveyRepository:
    def __init__(
        self,
        questions: Sequence[Question] = (),
        options: Sequence[AnswerOption] = (),
        *,
        users: FakeUserRepository | None = None,
        interests: FakeInterestRepository | None = None,
    ) -> None:
        self.questions = list(questions)
        self.options = list(options)
        self.answers: list[UserAnswer] = []
        self.users = users if users is not None else FakeUserRepository()
        self.interests = interests if interests is not None else FakeInterestRepository()

    async def list_active_text_answers(self, user_ids: Sequence[int]) -> Sequence[UserAnswer]:
        active = {q.id: q for q in self.questions if q.is_active and q.type == QuestionType.TEXT}
        return sorted(
            (
                a
                for a in self.answers
                if a.user_id in user_ids and a.question_id in active and a.text_value is not None
            ),
            key=lambda a: (active[a.question_id].order, a.question_id),
        )

    async def list_matching_candidate_ids(
        self, interest_ids: set[int], office_id: int, excluded_user_ids: set[int], limit: int
    ) -> Sequence[int]:
        user_interests = await self.interests.get_user_interest_ids(list(self.users.items))
        return sorted(
            u.id
            for u in self.users.items.values()
            if u.survey_completed
            and u.id not in excluded_user_ids
            and (u.office_id == office_id or bool(user_interests[u.id] & interest_ids))
        )[:limit]

    async def list_active_questions(self) -> Sequence[Question]:
        return sorted((q for q in self.questions if q.is_active), key=lambda q: (q.order, q.id))

    async def list_options(self, question_ids: Sequence[int]) -> Sequence[AnswerOption]:
        return sorted(
            (o for o in self.options if o.question_id in question_ids),
            key=lambda o: (o.order, o.id),
        )

    async def list_active_answers(self, user_id: int) -> Sequence[UserAnswer]:
        active = {q.id for q in self.questions if q.is_active}
        return [a for a in self.answers if a.user_id == user_id and a.question_id in active]

    async def replace_active_answers(
        self, user_id: int, question_ids: Sequence[int], answers: Sequence[UserAnswer]
    ) -> None:
        self.answers = [
            a for a in self.answers if a.user_id != user_id or a.question_id not in question_ids
        ]
        self.answers.extend(answers)


class FakeInterestRepository:
    def __init__(self, interests: Sequence[Interest] = ()) -> None:
        self.items = {i.id: i for i in interests}
        self.user_interests: dict[int, set[int]] = {}

    async def list_approved(self) -> Sequence[Interest]:
        return sorted(
            (i for i in self.items.values() if i.is_approved), key=lambda i: (i.name, i.id)
        )

    async def replace_user_interests(self, user_id: int, interest_ids: set[int]) -> None:
        self.user_interests[user_id] = set(interest_ids)

    async def get_user_interest_ids(self, user_ids: Sequence[int]) -> dict[int, set[int]]:
        visible = {i.id: i for i in await self.list_approved()}
        approved = {
            i.id
            for i in visible.values()
            if i.parent_id in visible and visible[i.parent_id].parent_id is None
        }
        return {u: self.user_interests.get(u, set()) & approved for u in user_ids}


class FakeMatchRepository:
    def __init__(self, matches: Sequence[Match] = ()) -> None:
        self.items = list(matches)

    async def excluded_user_ids(self, user_id: int) -> set[int]:
        result = {user_id}
        for match in self.items:
            if user_id not in (match.user_a_id, match.user_b_id):
                continue
            own = match.a_status if user_id == match.user_a_id else match.b_status
            if own == MatchStatus.ACCEPTED or MatchStatus.DECLINED in (
                match.a_status,
                match.b_status,
            ):
                result.add(match.user_b_id if user_id == match.user_a_id else match.user_a_id)
        return result
