"""Фейковые репозитории в памяти для unit-тестов сервисов.

Каждый фейк реализует тот же Protocol из app/repositories/interfaces.py,
что и настоящий Sql*Repository. Если добавляешь метод в Protocol — добавь его и сюда.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.errors import RateLimitedError
from app.models import ChatType, Office, User
from app.repositories.interfaces import ChatSummaryView, MessageView, UserBriefView
from app.services.notifier import ChatEvent


class FakeUserRepository:
    def __init__(self) -> None:
        self.items: dict[int, User] = {}
        self._next_id = 1

    async def get_by_id(self, user_id: int) -> User | None:
        return self.items.get(user_id)

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


@dataclass
class FakeChat:
    id: int
    type: ChatType
    group_id: int | None
    direct_key: str | None
    created_at: datetime


class FakeChatRepository:
    """Та же семантика, что у SqlChatRepository: идемпотентность, курсоры, unread."""

    def __init__(
        self, user_names: dict[int, str] | None = None, group_names: dict[int, str] | None = None
    ) -> None:
        self.user_names: dict[int, str] = user_names or {}
        self.group_names: dict[int, str] = group_names or {}
        self.chats: dict[int, FakeChat] = {}
        self.members: dict[tuple[int, int], int | None] = {}  # (chat_id, user_id) → last_read
        self.messages: list[MessageView] = []
        self.locked: list[int] = []
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)

    def _now(self) -> datetime:
        self._clock += timedelta(seconds=1)
        return self._clock

    def _brief(self, user_id: int) -> UserBriefView:
        return UserBriefView(id=user_id, name=self.user_names.get(user_id, f"user{user_id}"))

    def _add_chat(self, type_: ChatType, group_id: int | None, key: str | None) -> FakeChat:
        chat = FakeChat(len(self.chats) + 1, type_, group_id, key, self._now())
        self.chats[chat.id] = chat
        return chat

    def _summary(self, user_id: int, chat: FakeChat) -> ChatSummaryView:
        last_read = self.members[(chat.id, user_id)]
        chat_messages = [m for m in self.messages if m.chat_id == chat.id]
        peer_id = next((u for (c, u) in self.members if c == chat.id and u != user_id), None)
        return ChatSummaryView(
            id=chat.id,
            type=chat.type,
            group_id=chat.group_id,
            group_name=self.group_names.get(chat.group_id) if chat.group_id else None,
            peer=self._brief(peer_id)
            if chat.type == ChatType.DIRECT and peer_id is not None
            else None,
            last_message=chat_messages[-1] if chat_messages else None,
            unread_count=sum(
                1 for m in chat_messages if m.id > (last_read or 0) and m.sender.id != user_id
            ),
            last_read_message_id=last_read,
            created_at=chat.created_at,
        )

    async def get_user_chat(self, user_id: int, chat_id: int) -> ChatSummaryView | None:
        chat = self.chats.get(chat_id)
        if chat is None or (chat_id, user_id) not in self.members:
            return None
        return self._summary(user_id, chat)

    async def list_user_chats(self, user_id: int, limit: int) -> Sequence[ChatSummaryView]:
        summaries = [
            self._summary(user_id, self.chats[c]) for (c, u) in self.members if u == user_id
        ]

        def activity(s: ChatSummaryView) -> tuple[datetime, int]:
            return (s.last_message.created_at if s.last_message else s.created_at, s.id)

        return sorted(summaries, key=activity, reverse=True)[:limit]

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        return (chat_id, user_id) in self.members

    async def list_member_ids(self, chat_id: int) -> Sequence[int]:
        return sorted(u for (c, u) in self.members if c == chat_id)

    async def get_or_create_direct(self, key: str, member_ids: tuple[int, int]) -> tuple[int, bool]:
        existing = next((c for c in self.chats.values() if c.direct_key == key), None)
        if existing is not None:
            return existing.id, False
        chat = self._add_chat(ChatType.DIRECT, None, key)
        await self.add_members(chat.id, member_ids)
        return chat.id, True

    async def get_or_create_group_chat(self, group_id: int) -> tuple[int, bool]:
        existing = await self.get_group_chat_id(group_id)
        if existing is not None:
            return existing, False
        return self._add_chat(ChatType.GROUP, group_id, None).id, True

    async def get_group_chat_id(self, group_id: int) -> int | None:
        return next((c.id for c in self.chats.values() if c.group_id == group_id), None)

    async def add_members(self, chat_id: int, user_ids: Sequence[int]) -> Sequence[int]:
        added = []
        for user_id in user_ids:
            if (chat_id, user_id) not in self.members:
                self.members[(chat_id, user_id)] = None
                added.append(user_id)
        return added

    async def remove_member(self, chat_id: int, user_id: int) -> bool:
        if (chat_id, user_id) not in self.members:
            return False
        del self.members[(chat_id, user_id)]
        return True

    async def lock_chat(self, chat_id: int) -> None:
        self.locked.append(chat_id)

    async def find_by_client_id(self, sender_id: int, client_msg_id: UUID) -> MessageView | None:
        return next(
            (
                m
                for m in self.messages
                if m.sender.id == sender_id and m.client_msg_id == client_msg_id
            ),
            None,
        )

    async def add_message(
        self, chat_id: int, sender_id: int, text: str, client_msg_id: UUID | None
    ) -> tuple[MessageView, bool]:
        if client_msg_id is not None:
            existing = await self.find_by_client_id(sender_id, client_msg_id)
            if existing is not None:
                return existing, False
        message = MessageView(
            id=len(self.messages) + 1,
            chat_id=chat_id,
            sender=self._brief(sender_id),
            text=text,
            created_at=self._now(),
            client_msg_id=client_msg_id,
        )
        self.messages.append(message)
        return message, True

    async def list_messages(
        self, chat_id: int, *, before_id: int | None, after_id: int | None, limit: int
    ) -> tuple[Sequence[MessageView], bool]:
        chat_messages = [m for m in self.messages if m.chat_id == chat_id]
        if after_id is not None:
            newer = [m for m in chat_messages if m.id > after_id]
            return newer[:limit], len(newer) > limit
        if before_id is not None:
            chat_messages = [m for m in chat_messages if m.id < before_id]
        return chat_messages[-limit:], len(chat_messages) > limit

    async def message_in_chat(self, chat_id: int, message_id: int) -> bool:
        return any(m.id == message_id and m.chat_id == chat_id for m in self.messages)

    async def advance_read(self, chat_id: int, user_id: int, message_id: int) -> int:
        current = self.members[(chat_id, user_id)]
        self.members[(chat_id, user_id)] = max(current or 0, message_id)
        return max(current or 0, message_id)


class FakeChatEventSink:
    def __init__(self) -> None:
        self.events: list[ChatEvent] = []

    def add(self, event: ChatEvent) -> None:
        self.events.append(event)


class FakeRateLimiter:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.deny = False

    def check(self, user_id: int) -> None:
        self.calls.append(user_id)
        if self.deny:
            raise RateLimitedError("rate_limited", "Слишком много сообщений, подожди немного")
