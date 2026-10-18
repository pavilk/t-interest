from sqlalchemy import or_, select
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
                    Match.a_status == MatchStatus.DECLINED,
                    Match.b_status == MatchStatus.DECLINED,
                    (Match.user_a_id == user_id) & (Match.a_status == MatchStatus.ACCEPTED),
                    (Match.user_b_id == user_id) & (Match.b_status == MatchStatus.ACCEPTED),
                ),
            )
        )
        return {user_id} | {b if a == user_id else a for a, b in rows}
