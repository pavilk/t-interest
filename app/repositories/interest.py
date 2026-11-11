from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Interest, UserInterest


class SqlInterestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_approved(self) -> Sequence[Interest]:
        return (
            await self._session.scalars(
                select(Interest).where(Interest.is_approved).order_by(Interest.name, Interest.id)
            )
        ).all()

    async def replace_user_interests(self, user_id: int, interest_ids: set[int]) -> None:
        await self._session.execute(delete(UserInterest).where(UserInterest.user_id == user_id))
        self._session.add_all(
            UserInterest(user_id=user_id, interest_id=i) for i in sorted(interest_ids)
        )
        await self._session.flush()

    async def get_user_interest_ids(self, user_ids: Sequence[int]) -> dict[int, set[int]]:
        root = aliased(Interest)
        rows = await self._session.execute(
            select(UserInterest.user_id, UserInterest.interest_id)
            .join(Interest, UserInterest.interest_id == Interest.id)
            .join(root, Interest.parent_id == root.id)
            .where(
                UserInterest.user_id.in_(user_ids),
                Interest.is_approved,
                root.is_approved,
                root.parent_id.is_(None),
            )
        )
        result = {user_id: set[int]() for user_id in user_ids}
        for user_id, interest_id in rows:
            result[user_id].add(interest_id)
        return result
