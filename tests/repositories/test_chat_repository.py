import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chat, ChatType, Message
from app.repositories.chat import SqlChatRepository
from tests.repositories.conftest import (
    TEST_DATABASE_URL,
    MakeUser,
    SessionFactory,
    make_group,
)

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")


@pytest.fixture
def repo(session: AsyncSession) -> SqlChatRepository:
    return SqlChatRepository(session)


async def users(make_user: MakeUser, session: AsyncSession, *names: str) -> list[int]:
    ids = [await make_user(n) for n in names]
    await session.commit()
    return ids


# --- гонки ---


async def test_parallel_get_or_create_direct_gives_one_chat(
    session_factory: SessionFactory, session: AsyncSession, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")

    async def create() -> tuple[int, bool]:
        async with session_factory() as s:
            result = await SqlChatRepository(s).get_or_create_direct("1:2", (anya, boris))
            await asyncio.sleep(0.05)  # держим транзакцию, чтобы вторая упёрлась в конфликт
            await s.commit()
            return result

    results = await asyncio.gather(create(), create())

    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    assert await SqlChatRepository(session).list_member_ids(results[0][0]) == [anya, boris]


async def test_parallel_add_message_with_same_client_id_gives_one_row(
    session_factory: SessionFactory, session: AsyncSession, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")
    chat_id, _ = await SqlChatRepository(session).get_or_create_direct("1:2", (anya, boris))
    await session.commit()
    client_msg_id = uuid4()

    async def send() -> bool:
        async with session_factory() as s:
            _, created = await SqlChatRepository(s).add_message(
                chat_id, anya, "привет", client_msg_id
            )
            await asyncio.sleep(0.05)
            await s.commit()
            return created

    results = await asyncio.gather(send(), send())

    assert sorted(results) == [False, True]
    page, _ = await SqlChatRepository(session).list_messages(
        chat_id, before_id=None, after_id=None, limit=10
    )
    assert len(page) == 1


# --- ограничения схемы ---


async def test_direct_chat_requires_key(session: AsyncSession) -> None:
    session.add(Chat(type=ChatType.DIRECT))

    with pytest.raises(IntegrityError, match="ck_chats_direct_chat_has_key"):
        await session.flush()


async def test_blank_message_rejected_by_db(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")
    chat_id, _ = await repo.get_or_create_direct("1:2", (anya, boris))
    session.add(Message(chat_id=chat_id, sender_id=anya, text="   "))

    with pytest.raises(IntegrityError, match="ck_messages_text_not_blank"):
        await session.flush()


async def test_client_msg_id_unique_per_sender_but_null_allowed(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")
    chat_id, _ = await repo.get_or_create_direct("1:2", (anya, boris))
    client_msg_id = uuid4()

    await repo.add_message(chat_id, anya, "без ключа", None)
    await repo.add_message(chat_id, anya, "без ключа", None)
    await repo.add_message(chat_id, boris, "тот же ключ, другой отправитель", client_msg_id)
    first, created = await repo.add_message(chat_id, anya, "с ключом", client_msg_id)
    again, created_again = await repo.add_message(chat_id, anya, "повтор", client_msg_id)

    assert created and not created_again
    assert again == first and again.text == "с ключом"
    page, _ = await repo.list_messages(chat_id, before_id=None, after_id=None, limit=10)
    assert len(page) == 4


# --- unread и прочитанное ---


async def test_unread_count_respects_last_read_and_own_messages(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")
    chat_id, _ = await repo.get_or_create_direct("1:2", (anya, boris))
    first, _ = await repo.add_message(chat_id, anya, "раз", None)
    await repo.add_message(chat_id, anya, "два", None)
    await repo.add_message(chat_id, boris, "ответ", None)

    assert await repo.advance_read(chat_id, boris, first.id) == first.id
    assert await repo.advance_read(chat_id, boris, first.id - 1) == first.id  # только вперёд

    boris_view = await repo.get_user_chat(boris, chat_id)
    anya_view = await repo.get_user_chat(anya, chat_id)
    assert boris_view is not None and boris_view.unread_count == 1  # «два»
    assert anya_view is not None and anya_view.unread_count == 1  # «ответ»
    assert boris_view.last_read_message_id == first.id


async def test_get_user_chat_hides_foreign_chat(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris, vera = await users(make_user, session, "Аня", "Борис", "Вера")
    chat_id, _ = await repo.get_or_create_direct("1:2", (anya, boris))

    assert await repo.get_user_chat(vera, chat_id) is None
    assert not await repo.is_member(chat_id, vera)
    assert await repo.is_member(chat_id, anya)


async def test_message_in_chat(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris, vera = await users(make_user, session, "Аня", "Борис", "Вера")
    chat_id, _ = await repo.get_or_create_direct("1:2", (anya, boris))
    other_id, _ = await repo.get_or_create_direct("1:3", (anya, vera))
    message, _ = await repo.add_message(other_id, anya, "привет", None)

    assert await repo.message_in_chat(other_id, message.id)
    assert not await repo.message_in_chat(chat_id, message.id)


# --- курсоры ---


async def test_cursors_and_has_more_at_boundaries(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")
    chat_id, _ = await repo.get_or_create_direct("1:2", (anya, boris))

    empty, empty_more = await repo.list_messages(chat_id, before_id=None, after_id=None, limit=3)
    assert list(empty) == [] and not empty_more

    ids = [(await repo.add_message(chat_id, anya, f"m{i}", None))[0].id for i in range(5)]

    last, more = await repo.list_messages(chat_id, before_id=None, after_id=None, limit=3)
    assert [m.id for m in last] == ids[2:] and more
    older, more = await repo.list_messages(chat_id, before_id=ids[2], after_id=None, limit=2)
    assert [m.id for m in older] == ids[:2] and not more  # ровно limit строк
    newer, more = await repo.list_messages(chat_id, before_id=None, after_id=ids[0], limit=2)
    assert [m.id for m in newer] == ids[1:3] and more
    tail, more = await repo.list_messages(chat_id, before_id=None, after_id=ids[2], limit=2)
    assert [m.id for m in tail] == ids[3:] and not more
    assert last[0].sender.name == "Аня"


# --- список чатов ---


async def test_list_user_chats_sorting_title_and_peer(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris, vera = await users(make_user, session, "Аня", "Борис", "Вера")
    group_id = await make_group(session, "Волейбол", created_by=anya)
    direct_boris, _ = await repo.get_or_create_direct("1:2", (anya, boris))
    direct_vera, _ = await repo.get_or_create_direct("1:3", (anya, vera))
    group_chat, created = await repo.get_or_create_group_chat(group_id)
    assert created and (await repo.get_or_create_group_chat(group_id)) == (group_chat, False)
    assert await repo.add_members(group_chat, [anya, boris]) == [anya, boris]
    assert await repo.add_members(group_chat, [anya]) == []
    await session.commit()  # created_at чатов — now() транзакции; сообщения — уже позже
    await repo.add_message(direct_boris, boris, "свежее", None)

    chats = await repo.list_user_chats(anya, limit=10)

    # direct_boris — по сообщению; среди чатов без сообщений больший id выше
    assert [c.id for c in chats] == [direct_boris, group_chat, direct_vera]
    by_id = {c.id: c for c in chats}
    assert by_id[direct_boris].peer is not None and by_id[direct_boris].peer.name == "Борис"
    assert by_id[direct_boris].last_message is not None
    assert by_id[direct_boris].last_message.text == "свежее"
    assert by_id[group_chat].group_name == "Волейбол" and by_id[group_chat].peer is None
    assert by_id[direct_vera].last_message is None


async def test_remove_member(
    session: AsyncSession, repo: SqlChatRepository, make_user: MakeUser
) -> None:
    anya, boris = await users(make_user, session, "Аня", "Борис")
    group_id = await make_group(session, "Театр", created_by=anya)
    chat_id, _ = await repo.get_or_create_group_chat(group_id)
    await repo.add_members(chat_id, [anya, boris])

    assert await repo.remove_member(chat_id, boris)
    assert not await repo.remove_member(chat_id, boris)
    assert await repo.list_member_ids(chat_id) == [anya]
    assert await repo.get_group_chat_id(group_id) == chat_id
