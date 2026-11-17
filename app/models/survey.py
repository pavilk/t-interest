from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum


class QuestionType(StrEnum):
    SINGLE = "single"
    MULTI = "multi"
    TEXT = "text"


class Interest(Base):
    """Дерево из двух уровней: направление (parent_id = NULL) → поднаправление."""

    __tablename__ = "interests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("interests.id"))
    # false, пока интерес из заявки на группу не одобрен модератором
    is_approved: Mapped[bool] = mapped_column(server_default=true())


# Имя уникально в пределах родителя без учёта регистра; NULL-родители тоже сравниваются.
Index(
    "uq_interests_lower_name_parent",
    func.lower(Interest.name),
    Interest.parent_id,
    unique=True,
    postgresql_nulls_not_distinct=True,
)


class UserInterest(Base):
    """Пишет только SurveyService: пересчитывается из ответов анкеты."""

    __tablename__ = "user_interests"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    interest_id: Mapped[int] = mapped_column(
        ForeignKey("interests.id", ondelete="CASCADE"), primary_key=True
    )


class Question(Base):
    """Вопросы не удаляются, а выключаются через is_active."""

    __tablename__ = "questions"
    __table_args__ = (CheckConstraint("stage BETWEEN 1 AND 3", name="valid_stage"),)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("code", f"legacy_{kwargs['id']}" if "id" in kwargs else uuid4().hex)
        kwargs.setdefault("stage", 1)
        kwargs.setdefault("is_required", kwargs.get("type") != QuestionType.TEXT)
        super().__init__(**kwargs)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    text: Mapped[str] = mapped_column(String(500))
    type: Mapped[QuestionType] = mapped_column(str_enum(QuestionType, "question_type"))
    order: Mapped[int]
    is_active: Mapped[bool] = mapped_column(server_default=true())
    stage: Mapped[int] = mapped_column(server_default="1")
    is_required: Mapped[bool]
    show_if_option_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "answer_options.id",
            use_alter=True,
            name="fk_questions_show_if_option_id_answer_options",
        )
    )


class AnswerOption(Base):
    __tablename__ = "answer_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(String(200))
    # выбор этого варианта даёт пользователю интерес
    interest_id: Mapped[int | None] = mapped_column(ForeignKey("interests.id"))
    category_interest_id: Mapped[int | None] = mapped_column(ForeignKey("interests.id"))
    order: Mapped[int]


class UserAnswer(Base):
    __tablename__ = "user_answers"
    __table_args__ = (
        CheckConstraint("(option_id IS NULL) <> (text_value IS NULL)", name="option_xor_text"),
        UniqueConstraint("user_id", "question_id", "option_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    option_id: Mapped[int | None] = mapped_column(ForeignKey("answer_options.id"))
    text_value: Mapped[str | None] = mapped_column(String(300))


class SurveyMigrationState(Base):
    """Configuration-only journal for reversible catalogue migrations; no user answers."""

    __tablename__ = "survey_migration_state"

    revision: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
