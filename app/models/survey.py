from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    true,
)
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

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(500))
    type: Mapped[QuestionType] = mapped_column(str_enum(QuestionType, "question_type"))
    order: Mapped[int]
    is_active: Mapped[bool] = mapped_column(server_default=true())


class AnswerOption(Base):
    __tablename__ = "answer_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(String(200))
    # выбор этого варианта даёт пользователю интерес
    interest_id: Mapped[int | None] = mapped_column(ForeignKey("interests.id"))
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
