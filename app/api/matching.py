from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.core.deps import MatchingServiceDep, SurveyCompletedUser
from app.schemas.errors import error_responses
from app.schemas.matching import LikeOut, PersonRecommendationOut

router = APIRouter(tags=["matching"], responses=error_responses(401, 403, 404, 422))

# users.id хранится в PostgreSQL INTEGER.
TargetUserId = Annotated[int, Path(ge=1, le=2_147_483_647)]


@router.get("/recommendations/people")
async def recommend_people(
    user: SurveyCompletedUser,
    service: MatchingServiceDep,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> list[PersonRecommendationOut]:
    return await service.recommend_people(user.id, limit)


@router.post("/matches/{user_id}/like", responses=error_responses(409))
async def like(
    user_id: TargetUserId, user: SurveyCompletedUser, service: MatchingServiceDep
) -> LikeOut:
    """Одно нажатие создаёт пару. До интеграции chat_id всегда null."""
    return await service.like(user.id, user_id)


@router.post(
    "/matches/{user_id}/skip",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(409),
)
async def skip(
    user_id: TargetUserId, user: SurveyCompletedUser, service: MatchingServiceDep
) -> None:
    """Скрыть человека только из своей выдачи. Созданная пара сохраняется."""
    await service.skip(user.id, user_id)
