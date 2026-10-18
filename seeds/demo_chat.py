"""Демо-данные для ручной проверки чата. Все люди и данные вымышленные.

Создаёт офис, трёх пользователей с заполненной анкетой, личный чат Ани и Бориса
и групповой чат «Волейбол» на троих. Повторный запуск ничего не дублирует.

    python -m seeds.demo_chat

Пока нет matching и groups, это единственный способ получить чаты: сид вызывает
те же методы ChatService, что потом будут вызывать эти модули.
"""

import asyncio

from sqlalchemy import select

from app.core.db import get_outbox, session_scope
from app.core.rate_limit import TokenBucketLimiter
from app.core.security import hash_password
from app.models import Group, GroupStatus, Interest, Office, User, UserRole
from app.repositories.chat import SqlChatRepository
from app.services.chat import ChatService

PASSWORD = "demo-password-123"
USERS = [
    ("anya@example.com", "Аня Демидова"),
    ("boris@example.com", "Борис Тестов"),
    ("vera@example.com", "Вера Примерова"),
]


async def main() -> None:
    async with session_scope() as session:
        office = await session.scalar(select(Office).where(Office.name == "Демо-офис"))
        if office is None:
            office = Office(city="Екатеринбург", name="Демо-офис")
            session.add(office)
            await session.flush()

        user_ids = []
        for email, name in USERS:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    password_hash=hash_password(PASSWORD),
                    name=name,
                    office_id=office.id,
                    role=UserRole.USER,
                    survey_completed=True,
                )
                session.add(user)
                await session.flush()
            user_ids.append(user.id)
        anya, boris, vera = user_ids

        group = await session.scalar(select(Group).where(Group.name == "Волейбол"))
        if group is None:
            sport = Interest(name="Спорт")
            session.add(sport)
            await session.flush()
            volleyball = Interest(name="Волейбол", parent_id=sport.id)
            session.add(volleyball)
            await session.flush()
            group = Group(
                name="Волейбол",
                description="Играем по средам после работы",
                interest_id=volleyball.id,
                created_by=anya,
                status=GroupStatus.APPROVED,
            )
            session.add(group)
            await session.flush()

        chats = ChatService(
            SqlChatRepository(session), get_outbox(session), TokenBucketLimiter(100, 100)
        )
        direct_id = await chats.ensure_direct_chat(anya, boris)
        group_chat_id = await chats.create_group_chat(group.id, [anya, boris, vera])

    print(f"Пользователи (пароль {PASSWORD}):")
    for email, name in USERS:
        print(f"  {email} — {name}")
    print(f"Личный чат Ани и Бориса: id={direct_id}; групповой «Волейбол»: id={group_chat_id}")


if __name__ == "__main__":
    asyncio.run(main())
