import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.repositories.seed import SqlSeedRepository
from seeds.data import INTERESTS, OFFICES, QUESTIONS


async def seed(session: AsyncSession) -> None:
    repository = SqlSeedRepository(session)
    await repository.lock()
    for city, name in OFFICES:
        await repository.ensure_office(city, name)
    interests: dict[tuple[str, str], int] = {}
    for direction, children in INTERESTS.items():
        root = await repository.ensure_interest(direction, None)
        for name in children:
            child = await repository.ensure_interest(name, root.id)
            interests[(direction, name)] = child.id
    for order, (text, question_type, options) in enumerate(QUESTIONS, start=1):
        question = await repository.ensure_question(text, question_type, order)
        for option_order, (label, interest) in enumerate(options, start=1):
            await repository.ensure_option(
                question.id, label, option_order, interests[interest] if interest else None
            )


async def main() -> None:
    factory = get_session_factory()
    try:
        async with factory.begin() as session:
            await seed(session)
        print("Демонстрационные справочники загружены.")
    finally:
        bind = factory.kw["bind"]
        await bind.dispose()


if __name__ == "__main__":
    asyncio.run(main())
