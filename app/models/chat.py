from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum


class ChatType(StrEnum):
    DIRECT = "direct"
    GROUP = "group"


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("(type = 'group') = (group_id IS NOT NULL)", name="group_chat_has_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[ChatType] = mapped_column(str_enum(ChatType, "chat_type"))
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), unique=True
    )
    # "<min_user_id>:<max_user_id>" для личного чата: между двумя людьми только один
    direct_key: Mapped[str | None] = mapped_column(String(50), unique=True)
    created_at: Mapped[datetime] = created_at_column()


class ChatMember(Base):
    __tablename__ = "chat_members"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_read_message_id: Mapped[int | None]
    joined_at: Mapped[datetime] = created_at_column()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_chat_id_id", "chat_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = created_at_column()
