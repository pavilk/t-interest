from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    # движок создаётся при первом запросе, а не при импорте: тестам без БД он не нужен
    engine = create_async_engine(get_settings().database_url, hide_parameters=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Unit of work на один HTTP-запрос.

    Единственное место, где вызывается commit. Репозитории и сервисы не коммитят:
    они только добавляют, меняют и читают объекты в этой сессии.

    Подключается только через `SessionDep` из app/core/deps.py (scope="function"),
    чтобы commit произошёл до отправки ответа клиенту.
    """
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
