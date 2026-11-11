from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, delete, exists, func, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Chat, ChatMember, ChatType, Group, Message, User
from app.repositories.interfaces import ChatSummaryView, MessageView, UserBriefView


class SqlChatRepository:
    """Чаты, участники и сообщения.

    Список и карточка чата собираются одним запросом с read-only join к users и groups
    (имя собеседника, название группы) — по аналогии с SearchRepository.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- чтение чатов ---

    async def get_user_chat(self, user_id: int, chat_id: int) -> ChatSummaryView | None:
        rows = await self._session.execute(self._summaries(user_id).where(Chat.id == chat_id))
        row = rows.one_or_none()
        return _to_summary(row) if row is not None else None

    async def list_user_chats(self, user_id: int, limit: int) -> Sequence[ChatSummaryView]:
        rows = await self._session.execute(self._summaries(user_id).limit(limit))
        return [_to_summary(row) for row in rows]

    async def is_member(self, chat_id: int, user_id: int) -> bool:
        query = select(exists().where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id))
        return bool(await self._session.scalar(query))

    async def list_member_ids(self, chat_id: int) -> Sequence[int]:
        query = (
            select(ChatMember.user_id)
            .where(ChatMember.chat_id == chat_id)
            .order_by(ChatMember.user_id)
        )
        return list(await self._session.scalars(query))

    # --- создание чатов и участники ---

    async def get_or_create_direct(self, key: str, member_ids: tuple[int, int]) -> tuple[int, bool]:
        # ON CONFLICT + повторный SELECT: две параллельные транзакции получат один чат
        chat_id = await self._session.scalar(
            insert(Chat)
            .values(type=ChatType.DIRECT, direct_key=key)
            .on_conflict_do_nothing(index_elements=[Chat.direct_key])
            .returning(Chat.id)
        )
        if chat_id is not None:
            await self.add_members(chat_id, member_ids)
            return chat_id, True
        existing = await self._session.scalar(select(Chat.id).where(Chat.direct_key == key))
        assert existing is not None
        return existing, False

    async def get_or_create_group_chat(self, group_id: int) -> tuple[int, bool]:
        chat_id = await self._session.scalar(
            insert(Chat)
            .values(type=ChatType.GROUP, group_id=group_id)
            .on_conflict_do_nothing(index_elements=[Chat.group_id])
            .returning(Chat.id)
        )
        if chat_id is not None:
            return chat_id, True
        existing = await self.get_group_chat_id(group_id)
        assert existing is not None
        return existing, False

    async def get_group_chat_id(self, group_id: int) -> int | None:
        return await self._session.scalar(select(Chat.id).where(Chat.group_id == group_id))

    async def add_members(self, chat_id: int, user_ids: Sequence[int]) -> Sequence[int]:
        if not user_ids:
            return []
        added = await self._session.scalars(
            insert(ChatMember)
            .values([{"chat_id": chat_id, "user_id": u} for u in user_ids])
            .on_conflict_do_nothing()
            .returning(ChatMember.user_id)
        )
        return sorted(added)

    async def remove_member(self, chat_id: int, user_id: int) -> bool:
        removed = await self._session.scalar(
            delete(ChatMember)
            .where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
            .returning(ChatMember.user_id)
        )
        return removed is not None

    # --- сообщения ---

    async def lock_chat(self, chat_id: int) -> None:
        await self._session.execute(select(Chat.id).where(Chat.id == chat_id).with_for_update())

    async def find_by_client_id(self, sender_id: int, client_msg_id: UUID) -> MessageView | None:
        return await self._one_message(
            and_(Message.sender_id == sender_id, Message.client_msg_id == client_msg_id)
        )

    async def add_message(
        self, chat_id: int, sender_id: int, text: str, client_msg_id: UUID | None
    ) -> tuple[MessageView, bool]:
        # NULL в client_msg_id не конфликтует, поэтому сообщения без ключа вставляются всегда
        message_id = await self._session.scalar(
            insert(Message)
            .values(chat_id=chat_id, sender_id=sender_id, text=text, client_msg_id=client_msg_id)
            .on_conflict_do_nothing(index_elements=[Message.sender_id, Message.client_msg_id])
            .returning(Message.id)
        )
        if message_id is not None:
            created = await self._one_message(Message.id == message_id)
            assert created is not None
            return created, True
        assert client_msg_id is not None
        existing = await self.find_by_client_id(sender_id, client_msg_id)
        assert existing is not None
        return existing, False

    async def list_messages(
        self, chat_id: int, *, before_id: int | None, after_id: int | None, limit: int
    ) -> tuple[Sequence[MessageView], bool]:
        query = _messages().where(Message.chat_id == chat_id)
        if after_id is not None:
            query = query.where(Message.id > after_id).order_by(Message.id)
        else:
            if before_id is not None:
                query = query.where(Message.id < before_id)
            query = query.order_by(Message.id.desc())
        # limit + 1: лишняя строка говорит, что в этом направлении есть ещё сообщения
        rows = list(await self._session.execute(query.limit(limit + 1)))
        has_more = len(rows) > limit
        items = [_to_message(row) for row in rows[:limit]]
        if after_id is None:
            items.reverse()
        return items, has_more

    async def message_in_chat(self, chat_id: int, message_id: int) -> bool:
        query = select(exists().where(Message.id == message_id, Message.chat_id == chat_id))
        return bool(await self._session.scalar(query))

    async def advance_read(self, chat_id: int, user_id: int, message_id: int) -> int:
        last_read = await self._session.scalar(
            update(ChatMember)
            .where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
            .values(
                last_read_message_id=func.greatest(
                    func.coalesce(ChatMember.last_read_message_id, 0), message_id
                )
            )
            .returning(ChatMember.last_read_message_id)
        )
        assert last_read is not None
        return int(last_read)

    # --- запросы ---

    async def _one_message(self, condition: Any) -> MessageView | None:
        row = (await self._session.execute(_messages().where(condition))).one_or_none()
        return _to_message(row) if row is not None else None

    @staticmethod
    def _summaries(user_id: int) -> Select[*tuple[Any, ...]]:
        me = aliased(ChatMember)
        sender = aliased(User)
        peer_member = aliased(ChatMember)
        peer = aliased(User)

        last = (
            select(
                Message.id,
                Message.chat_id,
                Message.sender_id,
                Message.text,
                Message.created_at,
                Message.client_msg_id,
            )
            .where(Message.chat_id == Chat.id)
            .order_by(Message.id.desc())
            .limit(1)
            .lateral("last_message")
        )
        unread = (
            select(func.count())
            .where(
                Message.chat_id == Chat.id,
                Message.id > func.coalesce(me.last_read_message_id, 0),
                Message.sender_id != user_id,
            )
            .scalar_subquery()
        )
        return (
            select(
                Chat.id,
                Chat.type,
                Chat.group_id,
                Chat.created_at,
                Group.name.label("group_name"),
                peer.id.label("peer_id"),
                peer.name.label("peer_name"),
                me.last_read_message_id,
                unread.label("unread_count"),
                last.c.id.label("msg_id"),
                last.c.chat_id.label("msg_chat_id"),
                last.c.sender_id.label("msg_sender_id"),
                sender.name.label("msg_sender_name"),
                last.c.text.label("msg_text"),
                last.c.created_at.label("msg_created_at"),
                last.c.client_msg_id.label("msg_client_msg_id"),
            )
            .select_from(me)
            .join(Chat, Chat.id == me.chat_id)
            .outerjoin(last, true())
            .outerjoin(sender, sender.id == last.c.sender_id)
            .outerjoin(
                peer_member,
                and_(
                    Chat.type == ChatType.DIRECT,
                    peer_member.chat_id == Chat.id,
                    peer_member.user_id != user_id,
                ),
            )
            .outerjoin(peer, peer.id == peer_member.user_id)
            .outerjoin(Group, Group.id == Chat.group_id)
            .where(me.user_id == user_id)
            .order_by(func.coalesce(last.c.created_at, Chat.created_at).desc(), Chat.id.desc())
        )


def _messages() -> Select[*tuple[Any, ...]]:
    return select(
        Message.id,
        Message.chat_id,
        Message.sender_id,
        User.name.label("sender_name"),
        Message.text,
        Message.created_at,
        Message.client_msg_id,
    ).join(User, User.id == Message.sender_id)


def _to_message(row: Any) -> MessageView:
    return MessageView(
        id=row.id,
        chat_id=row.chat_id,
        sender=UserBriefView(id=row.sender_id, name=row.sender_name),
        text=row.text,
        created_at=row.created_at,
        client_msg_id=row.client_msg_id,
    )


def _to_summary(row: Any) -> ChatSummaryView:
    last_message = None
    if row.msg_id is not None:
        last_message = MessageView(
            id=row.msg_id,
            chat_id=row.msg_chat_id,
            sender=UserBriefView(id=row.msg_sender_id, name=row.msg_sender_name),
            text=row.msg_text,
            created_at=row.msg_created_at,
            client_msg_id=row.msg_client_msg_id,
        )
    return ChatSummaryView(
        id=row.id,
        type=row.type,
        group_id=row.group_id,
        group_name=row.group_name,
        peer=UserBriefView(id=row.peer_id, name=row.peer_name) if row.peer_id is not None else None,
        last_message=last_message,
        unread_count=row.unread_count,
        last_read_message_id=row.last_read_message_id,
        created_at=row.created_at,
    )
