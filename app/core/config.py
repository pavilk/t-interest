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

    @property
    def allowed_domains(self) -> frozenset[str]:
        return frozenset(
            d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # jwt_secret приходит из окружения
