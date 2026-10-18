"""DI: здесь собираются репозитории и сервисы для роутеров.

Роутеры получают готовый сервис через `...ServiceDep` и ничего не знают про сессию и SQL.
В API-тестах подменяются `get_*_repository` (на фейки) и `get_settings`,
тогда база не нужна. Новый модуль добавляет сюда свои `get_*_repository` и `get_*_service`.
"""

from typing import Annotated

from fastapi import Depends
from fastapi.requests import HTTPConnection
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_outbox, get_session
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rate_limit import RateLimiter, TokenBucketLimiter
from app.core.security import decode_token
from app.models import User
from app.repositories.chat import SqlChatRepository
from app.repositories.interfaces import ChatRepository, OfficeRepository, UserRepository
from app.repositories.office import SqlOfficeRepository
from app.repositories.user import SqlUserRepository
from app.services.chat import ChatService
from app.services.identity import IdentityService
from app.services.notifier import ChatEventSink

# scope="function": commit в get_session выполняется до отправки ответа, а не после
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- репозитории ---


def get_user_repository(session: SessionDep) -> UserRepository:
    return SqlUserRepository(session)


def get_office_repository(session: SessionDep) -> OfficeRepository:
    return SqlOfficeRepository(session)


def get_chat_repository(session: SessionDep) -> ChatRepository:
    return SqlChatRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
OfficeRepositoryDep = Annotated[OfficeRepository, Depends(get_office_repository)]
ChatRepositoryDep = Annotated[ChatRepository, Depends(get_chat_repository)]


# --- события и лимиты ---


def get_chat_events(session: SessionDep) -> ChatEventSink:
    """Outbox сессии запроса: события уйдут в WS только после commit в get_session."""
    return get_outbox(session)


def get_rate_limiter(conn: HTTPConnection, settings: SettingsDep) -> RateLimiter:
    """Один лимитер на приложение: общий для REST и WS."""
    limiter: RateLimiter | None = getattr(conn.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = TokenBucketLimiter(settings.chat_rate_per_second, settings.chat_rate_burst)
        conn.app.state.rate_limiter = limiter
    return limiter


ChatEventsDep = Annotated[ChatEventSink, Depends(get_chat_events)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


# --- сервисы ---


def get_identity_service(
    users: UserRepositoryDep, offices: OfficeRepositoryDep, settings: SettingsDep
) -> IdentityService:
    return IdentityService(users, offices, settings)


def get_chat_service(
    chats: ChatRepositoryDep, events: ChatEventsDep, limiter: RateLimiterDep
) -> ChatService:
    return ChatService(chats, events, limiter)


IdentityServiceDep = Annotated[IdentityService, Depends(get_identity_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


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
