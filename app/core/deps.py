"""DI: здесь собираются репозитории и сервисы для роутеров.

Роутеры получают готовый сервис через `...ServiceDep` и ничего не знают про сессию и SQL.
В API-тестах подменяются `get_*_repository` (на фейки) и `get_settings`,
тогда база не нужна. Новый модуль добавляет сюда свои `get_*_repository` и `get_*_service`.
"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.models import User
from app.repositories.interest import SqlInterestRepository
from app.repositories.interfaces import (
    InterestRepository,
    MatchRepository,
    OfficeRepository,
    SurveyRepository,
    UserRepository,
)
from app.repositories.match import SqlMatchRepository
from app.repositories.office import SqlOfficeRepository
from app.repositories.survey import SqlSurveyRepository
from app.repositories.user import SqlUserRepository
from app.services.identity import IdentityService
from app.services.matching import MatchingService
from app.services.survey import SurveyService

# scope="function": commit в get_session выполняется до отправки ответа, а не после
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- репозитории ---


def get_user_repository(session: SessionDep) -> UserRepository:
    return SqlUserRepository(session)


def get_office_repository(session: SessionDep) -> OfficeRepository:
    return SqlOfficeRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
OfficeRepositoryDep = Annotated[OfficeRepository, Depends(get_office_repository)]


# --- сервисы ---


def get_identity_service(
    users: UserRepositoryDep, offices: OfficeRepositoryDep, settings: SettingsDep
) -> IdentityService:
    return IdentityService(users, offices, settings)


IdentityServiceDep = Annotated[IdentityService, Depends(get_identity_service)]


def get_survey_repository(session: SessionDep) -> SurveyRepository:
    return SqlSurveyRepository(session)


def get_interest_repository(session: SessionDep) -> InterestRepository:
    return SqlInterestRepository(session)


SurveyRepositoryDep = Annotated[SurveyRepository, Depends(get_survey_repository)]
InterestRepositoryDep = Annotated[InterestRepository, Depends(get_interest_repository)]


def get_survey_service(
    surveys: SurveyRepositoryDep, interests: InterestRepositoryDep, identity: IdentityServiceDep
) -> SurveyService:
    return SurveyService(surveys, interests, identity)


SurveyServiceDep = Annotated[SurveyService, Depends(get_survey_service)]


def get_match_repository(session: SessionDep) -> MatchRepository:
    return SqlMatchRepository(session)


MatchRepositoryDep = Annotated[MatchRepository, Depends(get_match_repository)]


def get_matching_service(
    matches: MatchRepositoryDep, survey: SurveyServiceDep, identity: IdentityServiceDep
) -> MatchingService:
    return MatchingService(matches, survey, identity)


MatchingServiceDep = Annotated[MatchingService, Depends(get_matching_service)]


# --- текущий пользователь ---

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    users: UserRepositoryDep,
    settings: SettingsDep,
) -> User:
    if credentials is None:
        raise UnauthorizedError("not_authenticated", "Нужна авторизация")
    user = await users.get_by_id(decode_token(credentials.credentials, "access", settings))
    if user is None:
        raise UnauthorizedError("invalid_token", "Токен недействителен")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_survey_completed(user: CurrentUser) -> User:
    """Для всех ручек после анкеты: рекомендации, группы, чаты, поиск."""
    if not user.survey_completed:
        raise ForbiddenError("survey_required", "Сначала заполни анкету")
    return user


SurveyCompletedUser = Annotated[User, Depends(require_survey_completed)]
