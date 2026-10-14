"""Все модели импортируются здесь, чтобы Base.metadata (и Alembic) видел каждую таблицу."""

from app.models.base import Base
from app.models.chat import Chat, ChatMember, ChatType, Message
from app.models.group import Group, GroupMember, GroupMemberRole, GroupStatus
from app.models.match import Match, MatchStatus
from app.models.survey import (
    AnswerOption,
    Interest,
    Question,
    QuestionType,
    UserAnswer,
    UserInterest,
)
from app.models.user import Office, User, UserRole

__all__ = [
    "AnswerOption",
    "Base",
    "Chat",
    "ChatMember",
    "ChatType",
    "Group",
    "GroupMember",
    "GroupMemberRole",
    "GroupStatus",
    "Interest",
    "Match",
    "MatchStatus",
    "Message",
    "Office",
    "Question",
    "QuestionType",
    "User",
    "UserAnswer",
    "UserInterest",
    "UserRole",
]
