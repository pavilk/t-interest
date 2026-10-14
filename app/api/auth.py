"""Ручки входа. Роутер только принимает запрос, зовёт сервис и отдаёт ответ."""

from fastapi import APIRouter, status

from app.core.deps import IdentityServiceDep
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from app.schemas.errors import error_responses

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(409, 422),
)
async def register(data: RegisterRequest, service: IdentityServiceDep) -> TokenPair:
    return await service.register(data)


@router.post("/login", responses=error_responses(401))
async def login(data: LoginRequest, service: IdentityServiceDep) -> TokenPair:
    return await service.login(data)


@router.post("/refresh", responses=error_responses(401))
async def refresh(data: RefreshRequest, service: IdentityServiceDep) -> TokenPair:
    return await service.refresh(data.refresh_token)
