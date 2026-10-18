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
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models import ChatType, Office, User


class UserRepository(Protocol):
    async def get_by_id(self, user_id: int) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def add(self, user: User) -> User: ...


class OfficeRepository(Protocol):
    async def get_by_id(self, office_id: int) -> Office | None: ...

    async def list_all(self) -> Sequence[Office]: ...


# --- chat ---


@dataclass(frozen=True)
class UserBriefView:
    id: int
    name: str


@dataclass(frozen=True)
class MessageView:
    id: int
    chat_id: int
    sender: UserBriefView
    text: str
    created_at: datetime
    client_msg_id: UUID | None


@dataclass(frozen=True)
class ChatSummaryView:
    """Чат глазами одного участника: собеседник, последнее сообщение, непрочитанные."""

    id: int
    type: ChatType
    group_id: int | None
    group_name: str | None
    peer: UserBriefView | None  # только для direct; None, если собеседника уже нет
    last_message: MessageView | None
    unread_count: int  # чужие сообщения с id > last_read_message_id
    last_read_message_id: int | None
    created_at: datetime


class ChatRepository(Protocol):
    # чтение чатов
    async def get_user_chat(self, user_id: int, chat_id: int) -> ChatSummaryView | None:
        """None, если чата нет или пользователь в нём не состоит."""
        ...

    async def list_user_chats(self, user_id: int, limit: int) -> Sequence[ChatSummaryView]:
        """По последней активности: coalesce(последнее сообщение, создание чата) desc, id desc."""
        ...

    async def is_member(self, chat_id: int, user_id: int) -> bool: ...

    async def list_member_ids(self, chat_id: int) -> Sequence[int]: ...

    # создание чатов и участники
    async def get_or_create_direct(self, key: str, member_ids: tuple[int, int]) -> tuple[int, bool]:
        """(chat_id, создан ли сейчас). Безопасно при гонке двух вызовов с одним ключом."""
        ...

    async def get_or_create_group_chat(self, group_id: int) -> tuple[int, bool]: ...

    async def get_group_chat_id(self, group_id: int) -> int | None: ...

    async def add_members(self, chat_id: int, user_ids: Sequence[int]) -> Sequence[int]:
        """Возвращает только реально добавленных: уже состоящие пропускаются."""
        ...

    async def remove_member(self, chat_id: int, user_id: int) -> bool: ...

    # сообщения
    async def lock_chat(self, chat_id: int) -> None:
        """Блокирует чат до конца транзакции: сообщения одного чата коммитятся в порядке id."""
        ...

    async def find_by_client_id(
        self, sender_id: int, client_msg_id: UUID
    ) -> MessageView | None: ...

    async def add_message(
        self, chat_id: int, sender_id: int, text: str, client_msg_id: UUID | None
    ) -> tuple[MessageView, bool]:
        """(сообщение, создано ли сейчас). При повторе client_msg_id возвращает существующее."""
        ...

    async def list_messages(
        self, chat_id: int, *, before_id: int | None, after_id: int | None, limit: int
    ) -> tuple[Sequence[MessageView], bool]:
        """(сообщения по возрастанию id, есть ли ещё в направлении курсора).

        Без курсора — последние limit, before_id — ближайшие старше, after_id — ближайшие новее.
        """
        ...

    async def message_in_chat(self, chat_id: int, message_id: int) -> bool: ...

    async def advance_read(self, chat_id: int, user_id: int, message_id: int) -> int:
        """Двигает last_read_message_id только вперёд и возвращает итоговое значение."""
        ...


# TODO: SurveyRepository, InterestRepository, MatchRepository, GroupRepository —
# по образцу выше. Методы выводи из контрактов в .claude/plans/backend-architecture/ARCHITECTURE.md.
