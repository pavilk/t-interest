from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import ChatType

MESSAGE_MAX_LENGTH = 4000
# сырой текст может быть чуть длиннее: пробелы по краям обрезает сервис, он же проверяет длину
RAW_TEXT_MAX_LENGTH = MESSAGE_MAX_LENGTH + 100


class UserBrief(BaseModel):
    id: int
    name: str


class MessageOut(BaseModel):
    id: int
    chat_id: int
    sender: UserBrief
    text: str
    created_at: datetime
    client_msg_id: UUID | None


class ChatOut(BaseModel):
    id: int
    type: ChatType
    title: str = Field(description="Для direct — имя собеседника, для group — название группы")
    group_id: int | None
    peer: UserBrief | None = Field(description="Собеседник, только для direct")
    last_message: MessageOut | None
    unread_count: int
    last_read_message_id: int | None
    created_at: datetime


class MessagePage(BaseModel):
    items: list[MessageOut] = Field(description="Всегда по возрастанию id")
    has_more: bool = Field(description="Есть ли ещё сообщения в направлении курсора")


class SendMessageRequest(BaseModel):
    text: str = Field(max_length=RAW_TEXT_MAX_LENGTH)
    client_msg_id: UUID | None = Field(
        default=None, description="Ключ идемпотентности: повтор вернёт уже созданное сообщение"
    )


class ReadRequest(BaseModel):
    message_id: int
