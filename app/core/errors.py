"""Доменные ошибки. Сервисы бросают их, слой api переводит в HTTP-коды.

`code` — машинный код для фронта (например `email_taken`), `message` — текст для человека.
Ни в одном из них не должно быть персональных данных.
"""


class DomainError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(DomainError):
    """Объекта нет или пользователю нельзя знать о его существовании (→ 404)."""


class ForbiddenError(DomainError):
    """Действие запрещено этому пользователю (→ 403)."""


class ConflictError(DomainError):
    """Конфликт с текущим состоянием: дубликат, повторное действие (→ 409)."""


class DomainValidationError(DomainError):
    """Входные данные корректны по формату, но нарушают бизнес-правило (→ 422)."""


class UnauthorizedError(DomainError):
    """Неверные учётные данные или токен (→ 401)."""


class RateLimitedError(DomainError):
    """Слишком много действий за короткое время (→ 429)."""
