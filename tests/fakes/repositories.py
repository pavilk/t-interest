"""Фейковые репозитории в памяти для unit-тестов сервисов.

Каждый фейк реализует тот же Protocol из app/repositories/interfaces.py,
что и настоящий Sql*Repository. Если добавляешь метод в Protocol — добавь его и сюда.
"""

from collections.abc import Sequence

from app.models import Office, User


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
