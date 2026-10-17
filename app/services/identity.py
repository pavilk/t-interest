from collections.abc import Sequence

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.models import Office, User, UserRole
from app.repositories.interfaces import OfficeRepository, UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, TokenPair
from app.schemas.user import MeOut, OfficeOut, UpdateMeRequest

# Хэш для сравнения, когда пользователя нет: вход по несуществующему email
# занимает столько же времени, сколько по существующему.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


class IdentityService:
    """Регистрация, вход, токены и профиль текущего пользователя."""

    def __init__(
        self, users: UserRepository, offices: OfficeRepository, settings: Settings
    ) -> None:
        self._users = users
        self._offices = offices
        self._settings = settings

    async def register(self, data: RegisterRequest) -> TokenPair:
        email = data.email.lower()
        domain = email.rsplit("@", 1)[1]
        if domain not in self._settings.allowed_domains:
            raise DomainValidationError(
                "email_domain_not_allowed", "Регистрация доступна только с корпоративной почты"
            )
        await self._get_office(data.office_id)
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("email_taken", "Пользователь с такой почтой уже есть")

        user = await self._users.add(
            User(
                email=email,
                password_hash=hash_password(data.password),
                name=data.name.strip(),
                office_id=data.office_id,
                role=UserRole.USER,
                survey_completed=False,
            )
        )
        return self._issue_tokens(user.id)

    async def login(self, data: LoginRequest) -> TokenPair:
        user = await self._users.get_by_email(data.email.lower())
        password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        if not verify_password(data.password, password_hash) or user is None:
            # одна ошибка для «нет пользователя» и «неверный пароль»
            raise UnauthorizedError("invalid_credentials", "Неверная почта или пароль")
        return self._issue_tokens(user.id)

    async def refresh(self, refresh_token: str) -> TokenPair:
        user_id = decode_token(refresh_token, "refresh", self._settings)
        if await self._users.get_by_id(user_id) is None:
            raise UnauthorizedError("invalid_token", "Токен недействителен")
        return self._issue_tokens(user_id)

    async def get_me(self, user_id: int) -> MeOut:
        user = await self._get_user(user_id)
        office = await self._get_office(user.office_id)
        return self._to_me(user, office)

    async def update_me(self, user_id: int, data: UpdateMeRequest) -> MeOut:
        user = await self._get_user(user_id)
        if data.office_id is not None:
            await self._get_office(data.office_id)
            user.office_id = data.office_id
        if data.name is not None:
            user.name = data.name.strip()
        if data.about is not None:
            user.about = data.about.strip() or None
        # изменения объекта попадут в БД при commit в get_session
        office = await self._get_office(user.office_id)
        return self._to_me(user, office)

    async def list_offices(self) -> list[OfficeOut]:
        return [OfficeOut.model_validate(o) for o in await self._offices.list_all()]

    async def lock_for_survey(self, user_id: int) -> None:
        if await self._users.get_by_id_for_update(user_id) is None:
            raise NotFoundError("user_not_found", "Пользователь не найден")

    async def mark_survey_completed(self, user_id: int) -> MeOut:
        user = await self._get_user(user_id)
        user.survey_completed = True
        return await self.get_me(user_id)

    async def get_locations(self, user_ids: Sequence[int]) -> dict[int, tuple[int, str]]:
        if not user_ids:
            return {}
        users = await self._users.list_by_ids(user_ids)
        offices = {office.id: office for office in await self._offices.list_all()}
        return {user.id: (user.office_id, offices[user.office_id].city) for user in users}

    async def _get_user(self, user_id: int) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("user_not_found", "Пользователь не найден")
        return user

    async def _get_office(self, office_id: int) -> Office:
        office = await self._offices.get_by_id(office_id)
        if office is None:
            raise DomainValidationError("office_not_found", "Офис не найден")
        return office

    def _issue_tokens(self, user_id: int) -> TokenPair:
        return TokenPair(
            access_token=create_token(user_id, "access", self._settings),
            refresh_token=create_token(user_id, "refresh", self._settings),
        )

    @staticmethod
    def _to_me(user: User, office: Office) -> MeOut:
        return MeOut(
            id=user.id,
            email=user.email,
            name=user.name,
            about=user.about,
            office=OfficeOut.model_validate(office),
            role=user.role,
            survey_completed=user.survey_completed,
        )
