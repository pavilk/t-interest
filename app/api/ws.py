"""WebSocket /api/v1/ws: одно соединение на вкладку, мультиплексирует все чаты пользователя.

Рукопожатие: accept → первый фрейм {"type": "auth", "token"} за ws_auth_timeout_seconds →
auth_ok. Каждый send/read выполняется в своей сессии БД (ChatUnits.chat_service).
В логи попадают только user_id, тип фрейма и коды: ни текста, ни токена.
"""

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.openapi.utils import get_openapi
from pydantic import TypeAdapter, ValidationError

from app.api.connections import (
    CLOSE_FORBIDDEN,
    CLOSE_NORMAL,
    CLOSE_TOO_BIG,
    CLOSE_UNAUTHORIZED,
    Connection,
    ConnectionManager,
)
from app.core.config import Settings
from app.core.deps import ChatUnits, ChatUnitsDep, SettingsDep
from app.core.errors import DomainError, ForbiddenError, UnauthorizedError
from app.schemas.ws import (
    AckFrame,
    AuthFrame,
    AuthOkFrame,
    ClientFrame,
    ErrorFrame,
    PingFrame,
    PongFrame,
    ReadFrame,
    SendFrame,
    ServerFrame,
    client_frame_adapter,
)
from app.services.identity import AuthenticatedUser

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FRAME_CHARS = 16 * 1024  # основной лимит — uvicorn --ws-max-size, это страховка


class _Closed(Exception):
    def __init__(self, code: int) -> None:
        self.code = code


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket, units: ChatUnitsDep, settings: SettingsDep) -> None:
    manager: ConnectionManager = websocket.app.state.connections
    await websocket.accept()
    try:
        identity = await _authenticate(websocket, units, settings)
    except _Closed as closed:
        logger.info("ws auth rejected: code=%s", closed.code)
        await websocket.close(code=closed.code)
        return

    conn = Connection(websocket, identity.user_id, settings.ws_send_queue_size)
    manager.register(conn, settings.ws_max_connections_per_user)
    manager.send_to(conn, AuthOkFrame(user_id=identity.user_id))
    logger.info("ws connected: user_id=%s", conn.user_id)

    writer = asyncio.create_task(conn.run_writer())
    reader = asyncio.create_task(
        _read_loop(conn, manager, units, settings, identity.expires_at.timestamp())
    )
    try:
        done, _ = await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_COMPLETED)
        if reader in done and conn.close_code is not None:
            await writer  # писатель отправит close с нужным кодом
    finally:
        reader.cancel()
        writer.cancel()
        manager.unregister(conn)
        logger.info("ws disconnected: user_id=%s code=%s", conn.user_id, conn.close_code)


async def _authenticate(
    websocket: WebSocket, units: ChatUnits, settings: Settings
) -> AuthenticatedUser:
    try:
        message = await asyncio.wait_for(websocket.receive(), settings.ws_auth_timeout_seconds)
    except TimeoutError:
        raise _Closed(CLOSE_UNAUTHORIZED) from None
    text = message.get("text")
    if text is None:  # disconnect или бинарный фрейм
        raise _Closed(CLOSE_UNAUTHORIZED)
    try:
        frame = client_frame_adapter.validate_json(text)
    except ValidationError:
        raise _Closed(CLOSE_UNAUTHORIZED) from None
    if not isinstance(frame, AuthFrame):
        raise _Closed(CLOSE_UNAUTHORIZED)
    try:
        return await units.authenticate(frame.token)
    except UnauthorizedError:
        raise _Closed(CLOSE_UNAUTHORIZED) from None
    except ForbiddenError:
        raise _Closed(CLOSE_FORBIDDEN) from None


async def _read_loop(
    conn: Connection,
    manager: ConnectionManager,
    units: ChatUnits,
    settings: Settings,
    token_expires_at: float,
) -> None:
    """Читает фреймы до отключения клиента или до закрытия по таймауту."""
    while conn.close_code is None:
        until_expiry = token_expires_at - time.time()
        if until_expiry <= 0:  # токен истёк: переподключение со свежим токеном
            conn.request_close(CLOSE_UNAUTHORIZED)
            return
        try:
            message = await asyncio.wait_for(
                conn.websocket.receive(), min(settings.ws_idle_timeout_seconds, until_expiry)
            )
        except TimeoutError:
            if time.time() < token_expires_at:
                conn.request_close(CLOSE_NORMAL)  # клиент молчит дольше idle-таймаута
            continue
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is not None and len(text) > MAX_FRAME_CHARS:
            conn.request_close(CLOSE_TOO_BIG)
            return
        await _handle(conn, manager, units, text)


async def _handle(
    conn: Connection, manager: ConnectionManager, units: ChatUnits, text: str | None
) -> None:
    try:
        frame: ClientFrame = client_frame_adapter.validate_json(text or "")
    except ValidationError:
        # без деталей валидации: в них мог бы оказаться текст сообщения
        manager.send_to(conn, ErrorFrame(code="bad_frame", message="Некорректный фрейм"))
        return

    reply: ServerFrame
    try:
        match frame:
            case PingFrame():
                reply = PongFrame()
            case SendFrame():
                async with units.chat_service() as service:
                    message = await service.send_message(
                        conn.user_id, frame.chat_id, frame.text, frame.client_msg_id
                    )
                # commit уже случился, message разослан участникам — теперь ack
                reply = AckFrame(client_msg_id=frame.client_msg_id, message=message)
            case ReadFrame():
                async with units.chat_service() as service:
                    await service.mark_read(conn.user_id, frame.chat_id, frame.message_id)
                return  # ответ — фрейм read во все вкладки, его разослал outbox
            case AuthFrame():
                reply = ErrorFrame(code="bad_frame", message="Соединение уже авторизовано")
    except DomainError as exc:
        logger.info("ws frame rejected: type=%s code=%s", frame.type, exc.code)
        reply = _error(frame, exc.code, exc.message)
    except Exception:
        logger.exception("ws frame failed: type=%s user_id=%s", frame.type, conn.user_id)
        reply = _error(frame, "internal_error", "Внутренняя ошибка")
    manager.send_to(conn, reply)


def _error(frame: ClientFrame, code: str, message: str) -> ErrorFrame:
    return ErrorFrame(
        code=code,
        message=message,
        client_msg_id=frame.client_msg_id if isinstance(frame, SendFrame) else None,
        chat_id=frame.chat_id if isinstance(frame, SendFrame | ReadFrame) else None,
    )


def add_ws_schemas(app: FastAPI) -> None:
    """Добавляет ClientFrame и ServerFrame в components.schemas OpenAPI."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        adapters: dict[str, TypeAdapter[Any]] = {
            "ClientFrame": client_frame_adapter,
            "ServerFrame": TypeAdapter(ServerFrame),
        }
        for name, adapter in adapters.items():
            frame_schema = adapter.json_schema(
                ref_template="#/components/schemas/{model}", mode="serialization"
            )
            for def_name, definition in frame_schema.pop("$defs", {}).items():
                components.setdefault(def_name, definition)
            components[name] = frame_schema
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
