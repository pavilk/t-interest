"""Unit-тесты ChatService на фейках: репозиторий, outbox событий и лимитер."""

from uuid import uuid4

import pytest

from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    RateLimitedError,
)
from app.repositories.interfaces import ChatRepository
from app.schemas.chat import UserBrief
from app.services.chat import ChatService
from app.services.notifier import ChatCreated, ChatRemoved, MessageCreated, ReadUpdated
from tests.fakes.repositories import FakeChatEventSink, FakeChatRepository, FakeRateLimiter

ANYA, BORIS, VERA = 1, 2, 3

_: ChatRepository = FakeChatRepository()  # mypy: фейк соответствует Protocol


@pytest.fixture
def chats() -> FakeChatRepository:
    return FakeChatRepository(
        user_names={ANYA: "Аня", BORIS: "Борис", VERA: "Вера"}, group_names={10: "Волейбол"}
    )


@pytest.fixture
def events() -> FakeChatEventSink:
    return FakeChatEventSink()


@pytest.fixture
def limiter() -> FakeRateLimiter:
    return FakeRateLimiter()


@pytest.fixture
def service(
    chats: FakeChatRepository, events: FakeChatEventSink, limiter: FakeRateLimiter
) -> ChatService:
    return ChatService(chats, events, limiter)


@pytest.fixture
async def direct(service: ChatService, events: FakeChatEventSink) -> int:
    chat_id = await service.ensure_direct_chat(ANYA, BORIS)
    events.events.clear()
    return chat_id


# --- доступ ---


@pytest.mark.parametrize("foreign", [True, False])
async def test_foreign_and_missing_chat_are_indistinguishable(
    service: ChatService, direct: int, foreign: bool
) -> None:
    target = direct if foreign else 999
    calls = [
        service.get_chat(VERA, target),
        service.get_messages(VERA, target, before_id=None, after_id=None, limit=30),
        service.send_message(VERA, target, "привет", None),
        service.mark_read(VERA, target, 1),
    ]
    for call in calls:
        with pytest.raises(NotFoundError) as exc:
            await call
        assert exc.value.code == "chat_not_found"


# --- send_message ---


async def test_send_message_strips_text_and_publishes_to_all_members(
    service: ChatService, chats: FakeChatRepository, events: FakeChatEventSink, direct: int
) -> None:
    message = await service.send_message(ANYA, direct, "  привет  ", None)

    assert message.text == "привет"
    assert message.sender.name == "Аня"
    assert events.events == [MessageCreated(message=message, recipient_ids=(ANYA, BORIS))]
    assert chats.members[(direct, ANYA)] == message.id  # своё сообщение прочитано
    assert chats.locked == [direct]


@pytest.mark.parametrize(
    ("text", "code"), [("   ", "message_empty"), ("я" * 4001, "message_too_long")]
)
async def test_send_message_validates_text(
    service: ChatService, chats: FakeChatRepository, direct: int, text: str, code: str
) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await service.send_message(ANYA, direct, text, None)

    assert exc.value.code == code
    assert chats.messages == []


async def test_send_message_accepts_exactly_max_length(service: ChatService, direct: int) -> None:
    message = await service.send_message(ANYA, direct, " " + "я" * 4000 + " ", None)

    assert len(message.text) == 4000


async def test_repeated_client_msg_id_returns_same_message_once(
    service: ChatService,
    chats: FakeChatRepository,
    events: FakeChatEventSink,
    limiter: FakeRateLimiter,
    direct: int,
) -> None:
    client_msg_id = uuid4()

    first = await service.send_message(ANYA, direct, "привет", client_msg_id)
    second = await service.send_message(ANYA, direct, "привет", client_msg_id)

    assert first == second
    assert len(chats.messages) == 1
    assert len(events.events) == 1
    assert limiter.calls == [ANYA]


async def test_client_msg_id_from_another_chat_is_conflict(
    service: ChatService, chats: FakeChatRepository, direct: int
) -> None:
    other = await service.ensure_direct_chat(ANYA, VERA)
    client_msg_id = uuid4()
    await service.send_message(ANYA, direct, "привет", client_msg_id)

    with pytest.raises(ConflictError) as exc:
        await service.send_message(ANYA, other, "привет", client_msg_id)

    assert exc.value.code == "client_msg_id_reused"
    assert len(chats.messages) == 1


async def test_rate_limited_send_writes_nothing(
    service: ChatService,
    chats: FakeChatRepository,
    events: FakeChatEventSink,
    limiter: FakeRateLimiter,
    direct: int,
) -> None:
    limiter.deny = True

    with pytest.raises(RateLimitedError):
        await service.send_message(ANYA, direct, "привет", None)

    assert chats.messages == []
    assert events.events == []


# --- get_messages ---


async def test_get_messages_rejects_both_cursors(service: ChatService, direct: int) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await service.get_messages(ANYA, direct, before_id=5, after_id=1, limit=30)

    assert exc.value.code == "validation_error"


async def test_get_messages_pages_in_ascending_order(service: ChatService, direct: int) -> None:
    for i in range(5):
        await service.send_message(ANYA, direct, f"m{i}", None)

    last = await service.get_messages(ANYA, direct, before_id=None, after_id=None, limit=2)
    older = await service.get_messages(
        BORIS, direct, before_id=last.items[0].id, after_id=None, limit=2
    )
    newer = await service.get_messages(ANYA, direct, before_id=None, after_id=3, limit=10)

    assert [m.text for m in last.items] == ["m3", "m4"] and last.has_more
    assert [m.text for m in older.items] == ["m1", "m2"] and older.has_more
    assert [m.text for m in newer.items] == ["m3", "m4"] and not newer.has_more


# --- mark_read и unread ---


async def test_mark_read_moves_pointer_only_forward(
    service: ChatService, chats: FakeChatRepository, events: FakeChatEventSink, direct: int
) -> None:
    first = await service.send_message(ANYA, direct, "раз", None)
    second = await service.send_message(ANYA, direct, "два", None)
    events.events.clear()

    await service.mark_read(BORIS, direct, second.id)
    await service.mark_read(BORIS, direct, first.id)

    assert chats.members[(direct, BORIS)] == second.id
    assert events.events == [
        ReadUpdated(user_id=BORIS, chat_id=direct, message_id=second.id),
        ReadUpdated(user_id=BORIS, chat_id=direct, message_id=second.id),
    ]


async def test_mark_read_rejects_message_from_other_chat(service: ChatService, direct: int) -> None:
    other = await service.ensure_direct_chat(ANYA, VERA)
    foreign = await service.send_message(ANYA, other, "привет", None)

    with pytest.raises(DomainValidationError) as exc:
        await service.mark_read(BORIS, direct, foreign.id)

    assert exc.value.code == "message_not_in_chat"


async def test_unread_count_excludes_own_and_read_messages(
    service: ChatService, direct: int
) -> None:
    first = await service.send_message(ANYA, direct, "раз", None)
    await service.send_message(ANYA, direct, "два", None)
    await service.mark_read(BORIS, direct, first.id)

    assert (await service.get_chat(BORIS, direct)).unread_count == 1
    assert (await service.get_chat(ANYA, direct)).unread_count == 0  # свои не считаются

    await service.send_message(BORIS, direct, "ответ", None)

    anya_view = await service.get_chat(ANYA, direct)
    assert anya_view.unread_count == 1
    assert anya_view.last_message is not None and anya_view.last_message.text == "ответ"
    assert (await service.get_chat(BORIS, direct)).unread_count == 0  # ответ двигает last_read


async def test_chat_title_and_peer(service: ChatService, direct: int) -> None:
    group_chat = await service.create_group_chat(10, [ANYA, BORIS])

    chats = {c.id: c for c in await service.list_chats(ANYA)}

    assert chats[direct].title == "Борис"
    assert chats[direct].peer == UserBrief(id=BORIS, name="Борис")
    assert chats[group_chat].title == "Волейбол"
    assert chats[group_chat].peer is None


# --- ensure_direct_chat ---


async def test_ensure_direct_chat_is_idempotent_and_symmetric(
    service: ChatService, chats: FakeChatRepository, events: FakeChatEventSink
) -> None:
    first = await service.ensure_direct_chat(BORIS, ANYA)
    second = await service.ensure_direct_chat(ANYA, BORIS)

    assert first == second
    assert chats.chats[first].direct_key == "1:2"
    assert events.events == [ChatCreated(chat_id=first, user_ids=(BORIS, ANYA))]


async def test_cannot_chat_with_self(service: ChatService) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await service.ensure_direct_chat(ANYA, ANYA)

    assert exc.value.code == "cannot_chat_self"


# --- группы ---


async def test_group_membership_is_idempotent(
    service: ChatService, chats: FakeChatRepository, events: FakeChatEventSink
) -> None:
    chat_id = await service.create_group_chat(10, [ANYA])

    assert await service.add_group_member(10, BORIS) == chat_id
    await service.add_group_member(10, BORIS)
    await service.remove_group_member(10, BORIS)
    await service.remove_group_member(10, BORIS)
    await service.remove_group_member(99, BORIS)  # у группы нет чата: no-op

    assert events.events == [
        ChatCreated(chat_id=chat_id, user_ids=(ANYA,)),
        ChatCreated(chat_id=chat_id, user_ids=(BORIS,)),
        ChatRemoved(chat_id=chat_id, user_ids=(BORIS,)),
    ]
    assert await chats.list_member_ids(chat_id) == [ANYA]


async def test_add_member_to_group_without_chat(service: ChatService) -> None:
    with pytest.raises(NotFoundError) as exc:
        await service.add_group_member(99, ANYA)

    assert exc.value.code == "chat_not_found"
