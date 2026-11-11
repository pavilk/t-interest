"""Фреймы WebSocket /api/v1/ws. JSON-текст, поле `type` — дискриминатор.

FastAPI не видит WS-фреймы сам, поэтому `ClientFrame` и `ServerFrame`
добавляются в components.schemas OpenAPI (см. `app/api/ws.py`), и фронт
генерирует их типы вместе с REST.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

from app.schemas.chat import RAW_TEXT_MAX_LENGTH, MessageOut

# --- клиент → сервер ---


class AuthFrame(BaseModel):
    """Первый фрейм после подключения: токена в URL нет, чтобы он не попадал в логи."""

    type: Literal["auth"]
    token: str


class SendFrame(BaseModel):
    type: Literal["send"]
    chat_id: int
    text: str = Field(max_length=RAW_TEXT_MAX_LENGTH)
    client_msg_id: UUID = Field(description="Обязателен: по нему клиент сопоставит ack")


class ReadFrame(BaseModel):
    type: Literal["read"]
    chat_id: int
    message_id: int


class PingFrame(BaseModel):
    """Раз в 25 с: без входящих фреймов сервер закрывает соединение (1000)."""

    type: Literal["ping"]


ClientFrame = Annotated[AuthFrame | SendFrame | ReadFrame | PingFrame, Field(discriminator="type")]
client_frame_adapter: TypeAdapter[ClientFrame] = TypeAdapter(ClientFrame)


# --- сервер → клиент ---


class AuthOkFrame(BaseModel):
    type: Literal["auth_ok"] = "auth_ok"
    user_id: int


class MessageFrame(BaseModel):
    """Новое сообщение — всем онлайн-соединениям всех участников чата, включая отправителя."""

    type: Literal["message"] = "message"
    message: MessageOut


class AckFrame(BaseModel):
    """Подтверждение send отправившему соединению. Порядок с message не гарантирован."""

    type: Literal["ack"] = "ack"
    client_msg_id: UUID
    message: MessageOut


class ReadSyncFrame(BaseModel):
    """Прочитанное — во все вкладки прочитавшего, чтобы счётчик совпадал."""

    type: Literal["read"] = "read"
    chat_id: int
    message_id: int


class ChatCreatedFrame(BaseModel):
    """Пользователь стал участником чата; клиент запрашивает GET /chats/{chat_id}."""

    type: Literal["chat_created"] = "chat_created"
    chat_id: int


class ChatRemovedFrame(BaseModel):
    type: Literal["chat_removed"] = "chat_removed"
    chat_id: int


class ErrorFrame(BaseModel):
    """Ошибка обработки фрейма; соединение остаётся открытым."""

    type: Literal["error"] = "error"
    code: str
    message: str
    client_msg_id: UUID | None = None
    chat_id: int | None = None


class PongFrame(BaseModel):
    type: Literal["pong"] = "pong"


ServerFrame = Annotated[
    AuthOkFrame
    | MessageFrame
    | AckFrame
    | ReadSyncFrame
    | ChatCreatedFrame
    | ChatRemovedFrame
    | ErrorFrame
    | PongFrame,
    Field(discriminator="type"),
]
