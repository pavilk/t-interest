from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения и .env. Секреты только здесь, не в коде."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://tinterest:tinterest@localhost:5432/tinterest"
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    allowed_email_domains: str = "example.com"

    # чат и WebSocket
    ws_auth_timeout_seconds: float = 5
    ws_idle_timeout_seconds: float = 60
    ws_max_connections_per_user: int = 5
    ws_send_queue_size: int = 100
    chat_rate_per_second: float = 5
    chat_rate_burst: int = 10

    @property
    def allowed_domains(self) -> frozenset[str]:
        return frozenset(
            d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # jwt_secret приходит из окружения


class CorsSettings(BaseSettings):
    """Отдельно от Settings: читается при создании приложения, где JWT_SECRET ещё не нужен."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # origin'ы веба через запятую (например http://localhost:5173); пусто — CORS выключен
    cors_origins: str = ""

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
