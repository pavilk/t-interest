from collections.abc import Sequence

from sqlalchemy import Subquery, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerOption,
    Interest,
    Question,
    QuestionType,
    User,
    UserAnswer,
)
from app.repositories.interest import approved_user_leaves


def _approved_user_categories() -> Subquery:
    return (
        select(UserAnswer.user_id, Interest.id.label("interest_id"))
        .join(Question, Question.id == UserAnswer.question_id)
        .join(
            AnswerOption,
            (AnswerOption.id == UserAnswer.option_id) & (AnswerOption.question_id == Question.id),
        )
        .join(Interest, Interest.id == AnswerOption.category_interest_id)
        .where(
            Question.code == "v2_directions",
            Question.is_active,
            Interest.is_approved,
            Interest.parent_id.is_(None),
        )
        .subquery()
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
        self,
        interest_ids: set[int],
        category_ids: set[int],
        office_id: int,
        excluded_user_ids: set[int],
        limit: int,
    ) -> Sequence[int]:
        leaves = approved_user_leaves()
        categories = _approved_user_categories()
        common_interest = (
            select(1)
            .where(leaves.c.user_id == User.id, leaves.c.interest_id.in_(interest_ids))
            .exists()
        )
        common_category = (
            select(1)
            .where(categories.c.user_id == User.id, categories.c.interest_id.in_(category_ids))
            .exists()
        )
        return (
            await self._session.scalars(
                select(User.id)
                .where(
                    User.survey_completed,
                    User.id.not_in(excluded_user_ids),
                    or_(common_interest, common_category, User.office_id == office_id),
                )
                .order_by(common_interest.desc(), User.id)
                .limit(limit)
            )
        ).all()

    async def get_user_matching_interests(
        self, user_ids: Sequence[int]
    ) -> dict[int, list[Interest]]:
        if not user_ids:
            return {}
        leaves = approved_user_leaves()
        categories = _approved_user_categories()
        selected = (
            select(leaves)
            .where(leaves.c.user_id.in_(user_ids))
            .union(select(categories).where(categories.c.user_id.in_(user_ids)))
            .subquery()
        )
        rows = await self._session.execute(
            select(selected.c.user_id, Interest).join(
                Interest, Interest.id == selected.c.interest_id
            )
        )
        result: dict[int, list[Interest]] = {user_id: [] for user_id in user_ids}
        for user_id, interest in rows:
            result[user_id].append(interest)
        return result

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
