from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum


class MatchStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class Match(Base):
    """Реакции пары. Строка появляется при первом like/decline одной из сторон.

    Всегда user_a_id < user_b_id. Пара взаимная, когда оба статуса accepted.
    """

    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("user_a_id < user_b_id", name="ordered_pair"),
        UniqueConstraint("user_a_id", "user_b_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_a_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_b_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    score: Mapped[float]
    a_status: Mapped[MatchStatus] = mapped_column(
        str_enum(MatchStatus, "match_status"), server_default=MatchStatus.PENDING.value
    )
    b_status: Mapped[MatchStatus] = mapped_column(
        str_enum(MatchStatus, "match_status"), server_default=MatchStatus.PENDING.value
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
