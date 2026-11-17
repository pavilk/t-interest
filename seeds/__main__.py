import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.models import QuestionType
from app.repositories.seed import SqlSeedRepository
from seeds.data import INTERESTS, OFFICES, QUESTIONS


async def seed(session: AsyncSession) -> None:
    repository = SqlSeedRepository(session)
    await repository.lock()
    for city, name in OFFICES:
        await repository.ensure_office(city, name)
    interests: dict[tuple[str, str], int] = {}
    categories: dict[str, int] = {}
    for direction, children in INTERESTS.items():
        root = await repository.ensure_interest(direction, None)
        categories[direction] = root.id
        for name in children:
            child = await repository.ensure_interest(name, root.id)
            interests[(direction, name)] = child.id
    option_ids: dict[tuple[str, str], int] = {}
    for order, (code, text, kind, stage, required, trigger, options) in enumerate(QUESTIONS, 1):
        question = await repository.ensure_question(
            code,
            text,
            QuestionType(kind),
            order,
            stage,
            required,
            option_ids[trigger] if trigger else None,
        )
        for option_order, (label, interest) in enumerate(options, start=1):
            option = await repository.ensure_option(
                question.id,
                label,
                option_order,
                interests[interest] if interest else None,
                category_interest_id=categories.get(label) if code == "v2_directions" else None,
            )
            option_ids[(code, label)] = option.id


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
