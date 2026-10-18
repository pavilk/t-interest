from collections.abc import Sequence
from uuid import UUID

from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.rate_limit import RateLimiter
from app.models import ChatType
from app.repositories.interfaces import ChatRepository, ChatSummaryView, MessageView, UserBriefView
from app.schemas.chat import MESSAGE_MAX_LENGTH, ChatOut, MessageOut, MessagePage, UserBrief
from app.services.notifier import (
    ChatCreated,
    ChatEventSink,
    ChatRemoved,
    MessageCreated,
    ReadUpdated,
)

CHATS_LIST_LIMIT = 200
_DELETED_USER_TITLE = "Пользователь удалён"


def direct_key(user_a: int, user_b: int) -> str:
    return f"{min(user_a, user_b)}:{max(user_a, user_b)}"


class ChatService:
    """Личные и групповые чаты: история, отправка, прочитанное.

    Пользователь видит только чаты, в которых состоит; чужой и несуществующий чат
    неразличимы (chat_not_found). События уходят клиентам только после commit.
    """

    def __init__(self, chats: ChatRepository, events: ChatEventSink, limiter: RateLimiter) -> None:
        self._chats = chats
        self._events = events
        self._limiter = limiter

    # --- чтение ---

    async def list_chats(self, user_id: int) -> list[ChatOut]:
        return [_to_chat(c) for c in await self._chats.list_user_chats(user_id, CHATS_LIST_LIMIT)]

    async def get_chat(self, user_id: int, chat_id: int) -> ChatOut:
        chat = await self._chats.get_user_chat(user_id, chat_id)
        if chat is None:
            raise _chat_not_found()
        return _to_chat(chat)

    async def get_messages(
        self,
        user_id: int,
        chat_id: int,
        *,
        before_id: int | None,
        after_id: int | None,
        limit: int,
    ) -> MessagePage:
        await self._require_member(chat_id, user_id)
        if before_id is not None and after_id is not None:
            raise DomainValidationError(
                "validation_error", "Укажи только один курсор: before_id или after_id"
            )
        items, has_more = await self._chats.list_messages(
            chat_id, before_id=before_id, after_id=after_id, limit=limit
        )
        return MessagePage(items=[_to_message(m) for m in items], has_more=has_more)

    # --- запись пользователем ---

    async def send_message(
        self, user_id: int, chat_id: int, text: str, client_msg_id: UUID | None
    ) -> MessageOut:
        await self._require_member(chat_id, user_id)
        # повтор после обрыва: не валидируем, не считаем в лимит и не рассылаем повторно
        if client_msg_id is not None:
            existing = await self._chats.find_by_client_id(user_id, client_msg_id)
            if existing is not None:
                return _to_message(_same_chat(existing, chat_id))

        text = text.strip()
        if not text:
            raise DomainValidationError("message_empty", "Сообщение не может быть пустым")
        if len(text) > MESSAGE_MAX_LENGTH:
            raise DomainValidationError(
                "message_too_long", f"Сообщение длиннее {MESSAGE_MAX_LENGTH} символов"
            )
        self._limiter.check(user_id)

        # иначе параллельные коммиты в чат могли бы стать видимыми не в порядке id,
        # и догрузка по after_id пропустила бы сообщение
        await self._chats.lock_chat(chat_id)
        message, created = await self._chats.add_message(chat_id, user_id, text, client_msg_id)
        if not created:  # гонка двух одинаковых send: второй получает уже созданное
            return _to_message(_same_chat(message, chat_id))

        # своё сообщение не считается непрочитанным
        await self._chats.advance_read(chat_id, user_id, message.id)
        out = _to_message(message)
        recipients = tuple(await self._chats.list_member_ids(chat_id))
        self._events.add(MessageCreated(message=out, recipient_ids=recipients))
        return out

    async def mark_read(self, user_id: int, chat_id: int, message_id: int) -> None:
        await self._require_member(chat_id, user_id)
        if not await self._chats.message_in_chat(chat_id, message_id):
            raise DomainValidationError("message_not_in_chat", "Сообщение не из этого чата")
        last_read = await self._chats.advance_read(chat_id, user_id, message_id)
        self._events.add(ReadUpdated(user_id=user_id, chat_id=chat_id, message_id=last_read))

    # --- для matching ---

    async def ensure_direct_chat(self, user_a: int, user_b: int) -> int:
        """Личный чат пары; идемпотентно. Вызывается matching при взаимном лайке."""
        if user_a == user_b:
            raise DomainValidationError("cannot_chat_self", "Нельзя создать чат с самим собой")
        chat_id, created = await self._chats.get_or_create_direct(
            direct_key(user_a, user_b), (user_a, user_b)
        )
        if created:
            self._events.add(ChatCreated(chat_id=chat_id, user_ids=(user_a, user_b)))
        return chat_id

    # --- для groups ---

    async def create_group_chat(self, group_id: int, member_ids: Sequence[int]) -> int:
        """Чат группы при одобрении; повторный вызов только добавит недостающих участников."""
        chat_id, _ = await self._chats.get_or_create_group_chat(group_id)
        added = await self._chats.add_members(chat_id, member_ids)
        if added:
            self._events.add(ChatCreated(chat_id=chat_id, user_ids=tuple(added)))
        return chat_id

    async def add_group_member(self, group_id: int, user_id: int) -> int:
        chat_id = await self._chats.get_group_chat_id(group_id)
        if chat_id is None:
            raise _chat_not_found()
        if await self._chats.add_members(chat_id, [user_id]):
            self._events.add(ChatCreated(chat_id=chat_id, user_ids=(user_id,)))
        return chat_id

    async def remove_group_member(self, group_id: int, user_id: int) -> None:
        chat_id = await self._chats.get_group_chat_id(group_id)
        if chat_id is None:
            return
        if await self._chats.remove_member(chat_id, user_id):
            self._events.add(ChatRemoved(chat_id=chat_id, user_ids=(user_id,)))

    async def _require_member(self, chat_id: int, user_id: int) -> None:
        if not await self._chats.is_member(chat_id, user_id):
            raise _chat_not_found()


def _chat_not_found() -> NotFoundError:
    return NotFoundError("chat_not_found", "Чат не найден")


def _same_chat(message: MessageView, chat_id: int) -> MessageView:
    if message.chat_id != chat_id:
        raise ConflictError(
            "client_msg_id_reused", "Этот client_msg_id уже использован в другом чате"
        )
    return message


def _to_user(user: UserBriefView) -> UserBrief:
    return UserBrief(id=user.id, name=user.name)


def _to_message(message: MessageView) -> MessageOut:
    return MessageOut(
        id=message.id,
        chat_id=message.chat_id,
        sender=_to_user(message.sender),
        text=message.text,
        created_at=message.created_at,
        client_msg_id=message.client_msg_id,
    )


def _to_chat(chat: ChatSummaryView) -> ChatOut:
    if chat.type == ChatType.DIRECT:
        title = chat.peer.name if chat.peer is not None else _DELETED_USER_TITLE
    else:
        title = chat.group_name or ""
    return ChatOut(
        id=chat.id,
        type=chat.type,
        title=title,
        group_id=chat.group_id,
        peer=_to_user(chat.peer) if chat.peer is not None else None,
        last_message=_to_message(chat.last_message) if chat.last_message is not None else None,
        unread_count=chat.unread_count,
        last_read_message_id=chat.last_read_message_id,
        created_at=chat.created_at,
    )
