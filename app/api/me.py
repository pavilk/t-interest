from fastapi import APIRouter

from app.core.deps import CurrentUser, IdentityServiceDep
from app.schemas.errors import error_responses
from app.schemas.user import MeOut, OfficeOut, UpdateMeRequest

router = APIRouter(tags=["profile"], responses=error_responses(401))


@router.get("/me")
async def get_me(user: CurrentUser, service: IdentityServiceDep) -> MeOut:
    return await service.get_me(user.id)


@router.patch("/me", responses=error_responses(422))
async def update_me(data: UpdateMeRequest, user: CurrentUser, service: IdentityServiceDep) -> MeOut:
    return await service.update_me(user.id, data)


@router.get("/offices")
async def list_offices(user: CurrentUser, service: IdentityServiceDep) -> list[OfficeOut]:
    return await service.list_offices()
