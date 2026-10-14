from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum


class GroupStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GroupMemberRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class Group(Base):
    """Пользователям видны только approved; pending — автору и модераторам."""

    __tablename__ = "groups"
    __table_args__ = (
        # одна активная (pending или approved) группа на интерес
        Index(
            "uq_groups_active_interest",
            "interest_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), server_default="")
    interest_id: Mapped[int] = mapped_column(ForeignKey("interests.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[GroupStatus] = mapped_column(
        str_enum(GroupStatus, "group_status"), server_default=GroupStatus.PENDING.value
    )
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = created_at_column()


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[GroupMemberRole] = mapped_column(
        str_enum(GroupMemberRole, "group_member_role"),
        server_default=GroupMemberRole.MEMBER.value,
    )
