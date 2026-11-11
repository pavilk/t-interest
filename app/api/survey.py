from fastapi import APIRouter

from app.core.deps import CurrentUser, SurveyServiceDep
from app.schemas.errors import error_responses
from app.schemas.survey import InterestDirectionOut, QuestionOut, SurveyAnswer
from app.schemas.user import MeOut

router = APIRouter(tags=["survey"], responses=error_responses(401))


@router.get("/survey", summary="Получить анкету", responses=error_responses(409))
async def get_survey(user: CurrentUser, service: SurveyServiceDep) -> list[QuestionOut]:
    """Все активные вопросы по order/id с вариантами и условиями показа.

    Доступно до завершения анкеты. stage задаёт этап (1–3), is_required —
    обязательность доступного вопроса. show_if_option_id включает вопрос при выборе
    указанного варианта в доступном родительском вопросе; null означает безусловный
    показ. Клиент вычисляет видимость по текущему выбору. ID берутся из ответа.
    """
    return await service.get_survey()


@router.get("/survey/answers")
async def get_answers(user: CurrentUser, service: SurveyServiceDep) -> list[SurveyAnswer]:
    return await service.get_answers(user.id)


@router.put("/survey/answers", responses=error_responses(409, 422))
async def submit_answers(
    answers: list[SurveyAnswer], user: CurrentUser, service: SurveyServiceDep
) -> MeOut:
    """Полностью заменить ответы на активные вопросы.

    Обязательны только доступные is_required вопросы. Ответы скрытых вопросов
    отклоняются с question_not_applicable; пропущенные прежние ответы удаляются.
    Текстовые уточнения «Другое» отправляются отдельными ответами. Архивные ответы
    сохраняются. Ответы, интересы и survey_completed изменяются атомарно.
    """
    return await service.submit_answers(user.id, answers)


@router.get("/interests")
async def get_interests(user: CurrentUser, service: SurveyServiceDep) -> list[InterestDirectionOut]:
    return await service.list_interests()
