"""Интерфейсы репозиториев. Сервисы зависят только от них, а не от SQLAlchemy.

Реальные реализации — в соседних модулях (`SqlUserRepository` в user.py и т.д.),
фейки для unit-тестов — в tests/fakes/repositories.py.

Правила для каждого репозитория:
- принимает AsyncSession в конструкторе;
- никогда не вызывает commit (это делает get_session);
- вызывает flush, если после add нужен id;
- возвращает модели или простые значения, не Pydantic-схемы.
"""

from collections.abc import Sequence
from typing import Protocol

from app.models import AnswerOption, Interest, Match, Office, Question, User, UserAnswer


class UserRepository(Protocol):
    async def get_by_id(self, user_id: int) -> User | None: ...

    async def get_by_id_for_update(self, user_id: int) -> User | None: ...

    async def list_by_ids(self, user_ids: Sequence[int]) -> Sequence[User]: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def add(self, user: User) -> User: ...


class OfficeRepository(Protocol):
    async def get_by_id(self, office_id: int) -> Office | None: ...

    async def list_all(self) -> Sequence[Office]: ...


class SurveyRepository(Protocol):
    async def list_active_text_answers(self, user_ids: Sequence[int]) -> Sequence[UserAnswer]: ...

    async def list_matching_candidate_ids(
        self, interest_ids: set[int], office_id: int, excluded_user_ids: set[int], limit: int
    ) -> Sequence[int]: ...

    async def list_active_questions(self) -> Sequence[Question]: ...

    async def list_options(self, question_ids: Sequence[int]) -> Sequence[AnswerOption]: ...

    async def list_active_answers(self, user_id: int) -> Sequence[UserAnswer]: ...

    async def replace_active_answers(
        self, user_id: int, question_ids: Sequence[int], answers: Sequence[UserAnswer]
    ) -> None: ...


class InterestRepository(Protocol):
    async def list_approved(self) -> Sequence[Interest]: ...

    async def replace_user_interests(self, user_id: int, interest_ids: set[int]) -> None: ...

    async def get_user_interest_ids(self, user_ids: Sequence[int]) -> dict[int, set[int]]: ...


class MatchRepository(Protocol):
    async def excluded_user_ids(self, user_id: int) -> set[int]: ...

    async def get_or_create_for_update(self, user_a: int, user_b: int) -> Match: ...


# TODO: GroupRepository, ChatRepository —
# по образцу выше. Методы выводи из контрактов в .claude/plans/backend-architecture/ARCHITECTURE.md.
