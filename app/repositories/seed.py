"""Запись вымышленных справочников; транзакцией управляет seed-команда."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerOption, Interest, Office, Question, QuestionType


class SqlSeedRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock(self) -> None:
        # Natural keys for questions/options are not unique in the product schema.
        # Serialize cooperating seed processes without changing product constraints.
        await self._session.execute(select(func.pg_advisory_xact_lock(87134019)))

    async def ensure_office(self, city: str, name: str) -> Office:
        office = await self._session.scalar(
            select(Office).where(Office.city == city, Office.name == name)
        )
        if office is None:
            office = Office(city=city, name=name)
            self._session.add(office)
            await self._session.flush()
        return office

    async def ensure_interest(self, name: str, parent_id: int | None) -> Interest:
        interest = await self._session.scalar(
            select(Interest).where(
                func.lower(Interest.name) == name.lower(), Interest.parent_id == parent_id
            )
        )
        if interest is None:
            interest = Interest(name=name, parent_id=parent_id, is_approved=True)
            self._session.add(interest)
            await self._session.flush()
        return interest

    async def ensure_question(
        self,
        code: str,
        text: str,
        question_type: QuestionType,
        order: int,
        stage: int,
        is_required: bool,
        show_if_option_id: int | None,
    ) -> Question:
        question = await self._session.scalar(select(Question).where(Question.code == code))
        if question is None:
            question = Question(
                code=code,
                text=text,
                type=question_type,
                order=order,
                is_active=True,
                stage=stage,
                is_required=is_required,
                show_if_option_id=show_if_option_id,
            )
            self._session.add(question)
            await self._session.flush()
        return question

    async def ensure_option(
        self,
        question_id: int,
        text: str,
        order: int,
        interest_id: int | None,
        *,
        category_interest_id: int | None = None,
    ) -> AnswerOption:
        option = await self._session.scalar(
            select(AnswerOption).where(
                AnswerOption.question_id == question_id, AnswerOption.text == text
            )
        )
        if option is None:
            option = AnswerOption(
                question_id=question_id,
                text=text,
                order=order,
                interest_id=interest_id,
                category_interest_id=category_interest_id,
            )
            self._session.add(option)
            await self._session.flush()
        return option
