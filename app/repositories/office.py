from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Office


class SqlOfficeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, office_id: int) -> Office | None:
        return await self._session.get(Office, office_id)

    async def list_all(self) -> Sequence[Office]:
        result = await self._session.scalars(select(Office).order_by(Office.city, Office.name))
        return result.all()
