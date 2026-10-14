from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Формат любой ошибки API: {"error": {"code": "...", "message": "..."}}."""

    error: ErrorBody


def error_responses(*status_codes: int) -> dict[int | str, dict[str, object]]:
    """Для `responses=` в роутере: ошибки попадают в OpenAPI, фронт видит их типы."""
    return {code: {"model": ErrorResponse} for code in status_codes}
