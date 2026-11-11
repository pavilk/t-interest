from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, MatchStatus


class SqlMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def excluded_user_ids(self, user_id: int) -> set[int]:
        rows = await self._session.execute(
            select(Match.user_a_id, Match.user_b_id).where(
                or_(Match.user_a_id == user_id, Match.user_b_id == user_id),
                or_(
                    Match.a_status == MatchStatus.ACCEPTED,
                    Match.b_status == MatchStatus.ACCEPTED,
                    (Match.user_a_id == user_id) & (Match.a_status == MatchStatus.DECLINED),
                    (Match.user_b_id == user_id) & (Match.b_status == MatchStatus.DECLINED),
                ),
            )
        )
        return {user_id} | {b if a == user_id else a for a, b in rows}

    async def get_or_create_for_update(self, user_a: int, user_b: int) -> Match:
        """Блокировка канонической пары до завершения транзакции запроса."""
        user_a, user_b = sorted((user_a, user_b))
        await self._session.execute(
            insert(Match)
            .values(user_a_id=user_a, user_b_id=user_b, score=0.0)
            .on_conflict_do_nothing(index_elements=[Match.user_a_id, Match.user_b_id])
        )
        result = await self._session.execute(
            select(Match)
            .where(Match.user_a_id == user_a, Match.user_b_id == user_b)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one()
