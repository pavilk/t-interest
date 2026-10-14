"""Перевод доменных ошибок и ошибок валидации в единый JSON-формат."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import (
    ConflictError,
    DomainError,
    DomainValidationError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: 404,
    ForbiddenError: 403,
    ConflictError: 409,
    DomainValidationError: 422,
    UnauthorizedError: 401,
}


def _error(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message, **extra}}
    )


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    status = next((s for t, s in _STATUS_BY_ERROR.items() if isinstance(exc, t)), 400)
    response = _error(status, exc.code, exc.message)
    if status == 401:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # без поля "input": там может оказаться пароль или другие персональные данные
    fields = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return _error(422, "validation_error", "Некорректные данные запроса", fields=fields)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
