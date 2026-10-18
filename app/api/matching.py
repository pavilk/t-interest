from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import MatchingServiceDep, SurveyCompletedUser
from app.schemas.errors import error_responses
from app.schemas.matching import PersonRecommendationOut

router = APIRouter(tags=["matching"], responses=error_responses(401, 403, 422))


@router.get("/recommendations/people")
async def recommend_people(
    user: SurveyCompletedUser,
    service: MatchingServiceDep,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> list[PersonRecommendationOut]:
    return await service.recommend_people(user.id, limit)
