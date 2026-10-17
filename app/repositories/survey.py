from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerOption, Question, UserAnswer


class SqlSurveyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
