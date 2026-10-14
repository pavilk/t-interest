"""Unit-тесты IdentityService на фейковых репозиториях: без БД и без HTTP.

Образец для тестов остальных сервисов: собираем сервис из фейков,
вызываем метод, проверяем результат и состояние фейков, ошибки — по `code`.
"""

import pytest

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import create_token, decode_token
from app.models import Office, UserRole
from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.user import UpdateMeRequest
from app.services.identity import IdentityService
from tests.fakes.repositories import FakeOfficeRepository, FakeUserRepository

PASSWORD = "correct-horse-battery"


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def offices() -> FakeOfficeRepository:
    return FakeOfficeRepository(
        [
            Office(id=1, city="Екатеринбург", name="Офис на Ленина"),
            Office(id=2, city="Москва", name="Офис у реки"),
        ]
    )


@pytest.fixture
def service(
    users: FakeUserRepository, offices: FakeOfficeRepository, settings: Settings
) -> IdentityService:
    return IdentityService(users, offices, settings)


def register_request(email: str = "anya@example.com", office_id: int = 1) -> RegisterRequest:
    return RegisterRequest(email=email, password=PASSWORD, name="Аня", office_id=office_id)


# --- register ---


async def test_register_creates_user_and_returns_tokens(
    service: IdentityService, users: FakeUserRepository, settings: Settings
) -> None:
    tokens = await service.register(register_request())

    user = users.items[1]
    assert user.email == "anya@example.com"
    assert user.password_hash != PASSWORD
    assert user.role == UserRole.USER
    assert user.survey_completed is False
    assert decode_token(tokens.access_token, "access", settings) == user.id
    assert decode_token(tokens.refresh_token, "refresh", settings) == user.id


async def test_register_lowercases_email(
    service: IdentityService, users: FakeUserRepository
) -> None:
    await service.register(register_request(email="Anya@Example.COM"))

    assert users.items[1].email == "anya@example.com"


async def test_register_rejects_foreign_domain(service: IdentityService) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await service.register(register_request(email="anya@gmail.com"))

    assert exc.value.code == "email_domain_not_allowed"


async def test_register_rejects_unknown_office(service: IdentityService) -> None:
    with pytest.raises(DomainValidationError) as exc:
        await service.register(register_request(office_id=999))

    assert exc.value.code == "office_not_found"


async def test_register_rejects_taken_email_case_insensitive(service: IdentityService) -> None:
    await service.register(register_request(email="anya@example.com"))

    with pytest.raises(ConflictError) as exc:
        await service.register(register_request(email="ANYA@example.com"))

    assert exc.value.code == "email_taken"


# --- login ---


async def test_login_returns_tokens(service: IdentityService, settings: Settings) -> None:
    await service.register(register_request())

    tokens = await service.login(LoginRequest(email="ANYA@example.com", password=PASSWORD))

    assert decode_token(tokens.access_token, "access", settings) == 1


@pytest.mark.parametrize(
    ("email", "password"),
    [("anya@example.com", "wrong-password"), ("nobody@example.com", PASSWORD)],
    ids=["wrong_password", "unknown_email"],
)
async def test_login_fails_with_same_error(
    service: IdentityService, email: str, password: str
) -> None:
    await service.register(register_request())

    with pytest.raises(UnauthorizedError) as exc:
        await service.login(LoginRequest(email=email, password=password))

    assert exc.value.code == "invalid_credentials"


# --- refresh ---


async def test_refresh_issues_new_pair(service: IdentityService, settings: Settings) -> None:
    tokens = await service.register(register_request())

    new_tokens = await service.refresh(tokens.refresh_token)

    assert decode_token(new_tokens.access_token, "access", settings) == 1


async def test_refresh_rejects_access_token(service: IdentityService) -> None:
    tokens = await service.register(register_request())

    with pytest.raises(UnauthorizedError) as exc:
        await service.refresh(tokens.access_token)

    assert exc.value.code == "invalid_token"


async def test_refresh_rejects_garbage(service: IdentityService) -> None:
    with pytest.raises(UnauthorizedError):
        await service.refresh("not-a-jwt")


async def test_refresh_rejects_token_of_missing_user(
    service: IdentityService, settings: Settings
) -> None:
    token = create_token(42, "refresh", settings)

    with pytest.raises(UnauthorizedError):
        await service.refresh(token)


# --- me ---


async def test_get_me_returns_profile_with_office(service: IdentityService) -> None:
    await service.register(register_request())

    me = await service.get_me(1)

    assert me.email == "anya@example.com"
    assert me.office.city == "Екатеринбург"
    assert me.survey_completed is False


async def test_get_me_unknown_user(service: IdentityService) -> None:
    with pytest.raises(NotFoundError):
        await service.get_me(42)


async def test_update_me_changes_only_passed_fields(service: IdentityService) -> None:
    await service.register(register_request())

    me = await service.update_me(1, UpdateMeRequest(about="  Люблю бег и настолки  ", office_id=2))

    assert me.name == "Аня"
    assert me.about == "Люблю бег и настолки"
    assert me.office.city == "Москва"


async def test_list_offices_sorted_by_city(service: IdentityService) -> None:
    offices = await service.list_offices()

    assert [o.city for o in offices] == ["Екатеринбург", "Москва"]


async def test_update_me_rejects_unknown_office(
    service: IdentityService, users: FakeUserRepository
) -> None:
    await service.register(register_request())

    with pytest.raises(DomainValidationError):
        await service.update_me(1, UpdateMeRequest(office_id=999))

    assert users.items[1].office_id == 1
