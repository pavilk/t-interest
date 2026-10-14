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

from app.models import Office, User


class UserRepository(Protocol):
    async def get_by_id(self, user_id: int) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def add(self, user: User) -> User: ...


class OfficeRepository(Protocol):
    async def get_by_id(self, office_id: int) -> Office | None: ...

    async def list_all(self) -> Sequence[Office]: ...


# TODO: SurveyRepository, InterestRepository, MatchRepository, GroupRepository, ChatRepository —
# по образцу выше. Методы выводи из контрактов в .claude/plans/backend-architecture/ARCHITECTURE.md.
