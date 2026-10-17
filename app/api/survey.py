from fastapi import APIRouter

from app.core.deps import CurrentUser, SurveyServiceDep
from app.schemas.errors import error_responses
from app.schemas.survey import InterestDirectionOut, QuestionOut, SurveyAnswer
from app.schemas.user import MeOut

router = APIRouter(tags=["survey"], responses=error_responses(401))


@router.get("/survey")
async def get_survey(user: CurrentUser, service: SurveyServiceDep) -> list[QuestionOut]:
    return await service.get_survey()


@router.get("/survey/answers")
async def get_answers(user: CurrentUser, service: SurveyServiceDep) -> list[SurveyAnswer]:
    return await service.get_answers(user.id)


@router.put("/survey/answers", responses=error_responses(409, 422))
async def submit_answers(
    answers: list[SurveyAnswer], user: CurrentUser, service: SurveyServiceDep
) -> MeOut:
    return await service.submit_answers(user.id, answers)


@router.get("/interests")
async def get_interests(user: CurrentUser, service: SurveyServiceDep) -> list[InterestDirectionOut]:
    return await service.list_interests()
