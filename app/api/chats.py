from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import ChatServiceDep, SurveyCompletedUser
from app.schemas.chat import ChatOut, MessageOut, MessagePage, ReadRequest, SendMessageRequest
from app.schemas.errors import error_responses

router = APIRouter(prefix="/chats", tags=["chats"], responses=error_responses(401, 403))


@router.get("")
async def list_chats(user: SurveyCompletedUser, service: ChatServiceDep) -> list[ChatOut]:
    """Чаты пользователя, сверху — с самой свежей активностью."""
    return await service.list_chats(user.id)


@router.get("/{chat_id}", responses=error_responses(404))
async def get_chat(chat_id: int, user: SurveyCompletedUser, service: ChatServiceDep) -> ChatOut:
    return await service.get_chat(user.id, chat_id)


@router.get("/{chat_id}/messages", responses=error_responses(404, 422))
async def get_messages(
    chat_id: int,
    user: SurveyCompletedUser,
    service: ChatServiceDep,
    before_id: Annotated[int | None, Query(ge=1, description="Листать вверх: старше id")] = None,
    after_id: Annotated[
        int | None, Query(ge=0, description="Догрузка после переподключения: новее id")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> MessagePage:
    """История по возрастанию id. Курсоры before_id и after_id взаимоисключающие."""
    return await service.get_messages(
        user.id, chat_id, before_id=before_id, after_id=after_id, limit=limit
    )


@router.post(
    "/{chat_id}/messages",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(404, 409, 422, 429),
)
async def send_message(
    chat_id: int, data: SendMessageRequest, user: SurveyCompletedUser, service: ChatServiceDep
) -> MessageOut:
    """Повтор с тем же client_msg_id возвращает уже созданное сообщение."""
    return await service.send_message(user.id, chat_id, data.text, data.client_msg_id)


@router.post(
    "/{chat_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(404, 422),
)
async def mark_read(
    chat_id: int, data: ReadRequest, user: SurveyCompletedUser, service: ChatServiceDep
) -> None:
    await service.mark_read(user.id, chat_id, data.message_id)
