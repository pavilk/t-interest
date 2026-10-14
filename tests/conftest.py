import pytest

from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: тесты не зависят от локального .env
    return Settings(
        _env_file=None,
        jwt_secret="test-secret-at-least-32-bytes-long!!",
        allowed_email_domains="example.com",
    )
