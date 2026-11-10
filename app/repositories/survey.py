from collections.abc import Sequence

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import (
    AnswerOption,
    Interest,
    Question,
    QuestionType,
    User,
    UserAnswer,
    UserInterest,
)


class SqlSurveyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active_text_answers(self, user_ids: Sequence[int]) -> Sequence[UserAnswer]:
        if not user_ids:
            return []
        return (
            await self._session.scalars(
                select(UserAnswer)
                .join(Question)
                .where(
                    UserAnswer.user_id.in_(user_ids),
                    Question.is_active,
                    Question.type == QuestionType.TEXT,
                    UserAnswer.text_value.is_not(None),
                )
                .order_by(Question.order, Question.id, UserAnswer.id)
            )
        ).all()

    async def list_matching_candidate_ids(
        self, interest_ids: set[int], office_id: int, excluded_user_ids: set[int], limit: int
    ) -> Sequence[int]:
        root = aliased(Interest)
        common_interest = (
            select(UserInterest.user_id)
            .join(Interest, UserInterest.interest_id == Interest.id)
            .join(root, Interest.parent_id == root.id)
            .where(
                UserInterest.user_id == User.id,
                UserInterest.interest_id.in_(interest_ids),
                Interest.is_approved,
                root.is_approved,
                root.parent_id.is_(None),
            )
            .exists()
        )
        return (
            await self._session.scalars(
                select(User.id)
                .where(
                    User.survey_completed,
                    User.id.not_in(excluded_user_ids),
                    or_(User.office_id == office_id, common_interest),
                )
                .order_by(User.id)
                .limit(limit)
            )
        ).all()

    async def list_active_questions(self) -> Sequence[Question]:
        return (
            await self._session.scalars(
                select(Question).where(Question.is_active).order_by(Question.order, Question.id)
            )
        ).all()

    async def list_options(self, question_ids: Sequence[int]) -> Sequence[AnswerOption]:
        return (
            await self._session.scalars(
                select(AnswerOption)
                .where(AnswerOption.question_id.in_(question_ids))
                .order_by(AnswerOption.order, AnswerOption.id)
            )
        ).all()

    async def list_active_answers(self, user_id: int) -> Sequence[UserAnswer]:
        return (
            await self._session.scalars(
                select(UserAnswer)
                .join(Question)
                .where(UserAnswer.user_id == user_id, Question.is_active)
                .order_by(Question.order, Question.id, UserAnswer.option_id, UserAnswer.id)
            )
        ).all()

    async def replace_active_answers(
        self, user_id: int, question_ids: Sequence[int], answers: Sequence[UserAnswer]
    ) -> None:
        await self._session.execute(
            delete(UserAnswer).where(
                UserAnswer.user_id == user_id, UserAnswer.question_id.in_(question_ids)
            )
        )
        self._session.add_all(answers)
        # Ensure DELETE precedes INSERT on a repeated selection of the same unique key.
        await self._session.flush()
