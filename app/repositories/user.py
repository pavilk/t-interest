from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_id_for_update(self, user_id: int) -> User | None:
        # Refresh an object possibly loaded by authentication before waiting for the lock.
        return await self._session.scalar(
            select(User).where(User.id == user_id).with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_by_ids(self, user_ids: Sequence[int]) -> Sequence[User]:
        return (await self._session.scalars(select(User).where(User.id.in_(user_ids)))).all()

    async def get_by_email(self, email: str) -> User | None:
        return await self._session.scalar(select(User).where(User.email == email))

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()  # нужен user.id; commit сделает get_session
        return user
