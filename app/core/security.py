from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings
from app.core.errors import UnauthorizedError

TokenType = Literal["access", "refresh"]

_password_hash = PasswordHash.recommended()  # argon2


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def create_token(user_id: int, token_type: TokenType, settings: Settings) -> str:
    if token_type == "access":
        ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    else:
        ttl = timedelta(days=settings.refresh_token_ttl_days)
    payload = {"sub": str(user_id), "type": token_type, "exp": datetime.now(UTC) + ttl}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType, settings: Settings) -> int:
    """Возвращает id пользователя или бросает UnauthorizedError."""
    user_id, _ = decode_token_claims(token, expected_type, settings)
    return user_id


def decode_token_claims(
    token: str, expected_type: TokenType, settings: Settings
) -> tuple[int, datetime]:
    """(id пользователя, момент истечения токена) или UnauthorizedError."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("invalid_token", "Токен недействителен") from exc
    if payload.get("type") != expected_type:
        raise UnauthorizedError("invalid_token", "Токен недействителен")
    try:
        return int(payload["sub"]), datetime.fromtimestamp(payload["exp"], UTC)
    except (KeyError, ValueError, TypeError) as exc:
        raise UnauthorizedError("invalid_token", "Токен недействителен") from exc
