"""Shared fictional PostgreSQL fixtures; never connects without TEST_DATABASE_URL."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import AnswerOption, Interest, Office, Question, QuestionType, User, UserRole
from app.repositories.interest import SqlInterestRepository
from app.repositories.office import SqlOfficeRepository
from app.repositories.survey import SqlSurveyRepository
from app.repositories.user import SqlUserRepository
from app.services.identity import IdentityService
from app.services.survey import SurveyService


def survey_service(session: AsyncSession, settings: Settings) -> SurveyService:
    return SurveyService(
        SqlSurveyRepository(session),
        SqlInterestRepository(session),
        IdentityService(SqlUserRepository(session), SqlOfficeRepository(session), settings),
    )


async def populate(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        session.add(Office(id=1, city="Тестовый город", name="Тестовый офис"))
        session.add(Interest(id=10, name="Спорт", parent_id=None))
        session.add_all(
            [
                Question(id=1, text="Single", type=QuestionType.SINGLE, order=1),
                Question(id=2, text="Multi", type=QuestionType.MULTI, order=2),
                Question(id=3, text="Text", type=QuestionType.TEXT, order=3),
                Question(id=4, text="Archive", type=QuestionType.TEXT, order=0, is_active=False),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Interest(id=11, name="Бег", parent_id=10),
                Interest(id=12, name="Плавание", parent_id=10),
                Interest(id=13, name="Скрытый", parent_id=10, is_approved=False),
                User(
                    id=1,
                    email="first@example.com",
                    password_hash="unused",
                    name="Первый",
                    office_id=1,
                    role=UserRole.USER,
                ),
                User(
                    id=2,
                    email="second@example.com",
                    password_hash="unused",
                    name="Второй",
                    office_id=1,
                    role=UserRole.USER,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AnswerOption(id=1, question_id=1, text="A", order=1, interest_id=11),
                AnswerOption(id=2, question_id=1, text="B", order=2, interest_id=12),
                AnswerOption(id=3, question_id=2, text="C", order=1, interest_id=11),
                AnswerOption(id=4, question_id=2, text="D", order=2, interest_id=None),
            ]
        )
