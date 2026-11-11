import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.models import AnswerOption, Interest, Question, User, UserAnswer, UserInterest
from seeds.__main__ import seed

pytestmark = pytest.mark.postgres


async def migrate(session: AsyncSession, target: str, *, down: bool = False) -> None:
    connection = await session.connection()

    def run(sync_connection):
        config = Config("alembic.ini")
        config.attributes["connection"] = sync_connection
        (command.downgrade if down else command.upgrade)(config, target)

    await connection.run_sync(run)


async def test_fresh_catalogue_and_seed_are_consistent(pg_factory):
    async with pg_factory.begin() as session:
        questions = list((await session.scalars(select(Question).order_by(Question.order))).all())
        assert len(questions) == 10
        assert len({q.code for q in questions}) == 10
        assert len([q for q in questions if q.is_required]) == 8
        assert questions[0].text == "Что тебе интересно?"
        assert [
            o.text
            for o in (
                await session.scalars(
                    select(AnswerOption)
                    .where(AnswerOption.question_id == questions[0].id)
                    .order_by(AnswerOption.order)
                )
            ).all()
        ] == [
            "Спорт",
            "Культура и искусство",
            "Экология",
            "Животные",
            "Отдых",
            "Настольные игры",
            "Технологии",
            "Другое",
        ]
        before = [(q.id, q.code, q.show_if_option_id) for q in questions]
        await seed(session)
        await seed(session)
        after = list((await session.scalars(select(Question).order_by(Question.order))).all())
        assert [(q.id, q.code, q.show_if_option_id) for q in after] == before
        assert len((await session.scalars(select(Interest))).all()) == 20


async def test_upgrade_preserves_legacy_answers_and_completion(pg_factory):
    async with pg_factory.begin() as session:
        await migrate(session, "5f3ad79d9033", down=True)
        await session.execute(
            text("""
            INSERT INTO offices(id, city, name) VALUES(50, 'Test', 'Test');
        """)
        )
        await session.execute(
            text("""
            INSERT INTO users(id,email,password_hash,name,office_id,survey_completed)
            VALUES(50,'old@example.com','unused','Old',50,true)
        """)
        )
        await session.execute(
            text("""
            INSERT INTO interests(id,name,parent_id) VALUES(100,'Спорт',NULL),
            (101,'Бег',100),(110,'Искусство',NULL),(111,'Музыка',110)
        """)
        )
        await session.execute(
            text("""
            INSERT INTO questions(id,text,type,"order",is_active) VALUES
            (100,'Какой формат знакомства вам удобнее?','single',1,true),
            (101,'Какие занятия вам интересны?','multi',2,true),
            (102,'Какие ещё увлечения вы хотите указать?','text',3,false)
        """)
        )
        await session.execute(
            text("""
            INSERT INTO answer_options(id,question_id,text,"order",interest_id)
            VALUES(100,101,'Бег',1,101)
        """)
        )
        await session.execute(
            text("""
            INSERT INTO user_answers(user_id,question_id,option_id) VALUES(50,101,100)
        """)
        )
        await session.execute(text("INSERT INTO user_interests VALUES(50,101)"))
        await migrate(session, "head")
    async with pg_factory.begin() as session:
        assert (await session.get(User, 50)).survey_completed is True
        assert len((await session.scalars(select(UserAnswer))).all()) == 1
        assert (await session.scalars(select(UserInterest))).all() == []
        assert not (await session.get(Question, 101)).is_active
        assert (await session.get(Interest, 110)).name == "Культура и искусство"
        assert (await session.get(Interest, 101)).name == "Бег"
        await migrate(session, "5f3ad79d9033", down=True)
        assert (
            await session.execute(text("SELECT is_active FROM questions WHERE id=100"))
        ).scalar()
        assert not (
            await session.execute(text("SELECT is_active FROM questions WHERE id=102"))
        ).scalar()
        assert (
            await session.execute(text("SELECT name FROM interests WHERE id=110"))
        ).scalar() == "Искусство"
        assert (
            await session.execute(text("SELECT interest_id FROM user_interests"))
        ).scalar() == 101
        await migrate(session, "head")


async def test_downgrade_refuses_to_delete_v2_answers(pg_factory):
    async with pg_factory.begin() as session:
        await seed(session)
        office = (await session.execute(text("SELECT id FROM offices LIMIT 1"))).scalar_one()
        user = User(email="new@example.com", name="New", password_hash="unused", office_id=office)
        session.add(user)
        await session.flush()
        question = (
            await session.scalars(select(Question).where(Question.code == "v2_about"))
        ).one()
        session.add(UserAnswer(user_id=user.id, question_id=question.id, text_value="Keep me"))
    async with pg_factory.begin() as session:
        with pytest.raises(RuntimeError, match="restore a backup"):
            await migrate(session, "5f3ad79d9033", down=True)
    async with pg_factory() as session:
        assert (await session.scalars(select(UserAnswer.text_value))).one() == "Keep me"


async def test_unexpected_active_question_aborts_upgrade(pg_factory):
    async with pg_factory.begin() as session:
        await migrate(session, "5f3ad79d9033", down=True)
        await session.execute(
            text("""
            INSERT INTO questions(text,type,"order") VALUES('Custom','multi',1)
        """)
        )
    with pytest.raises(RuntimeError, match="Unexpected"):
        async with pg_factory.begin() as session:
            await migrate(session, "head")
    async with pg_factory() as session:
        assert (await session.execute(text("SELECT text FROM questions"))).scalar_one() == "Custom"


@pytest.mark.parametrize("usage", ["changed_configuration", "group"])
async def test_downgrade_protects_changed_catalogue_and_groups(pg_factory, usage):
    async with pg_factory.begin() as session:
        if usage == "changed_configuration":
            await session.execute(
                text("UPDATE questions SET is_active=false WHERE code='v2_about'")
            )
        else:
            await seed(session)
            user = User(
                email="owner@example.com", name="Owner", password_hash="unused", office_id=1
            )
            session.add(user)
            await session.flush()
            leaf = (
                await session.scalars(select(Interest).where(Interest.name == "Волейбол"))
            ).one()
            await session.execute(
                text("""
                INSERT INTO groups(name,description,interest_id,created_by,status)
                VALUES('Test group','Test',:interest,:user,'pending')
            """),
                {"interest": leaf.id, "user": user.id},
            )
    async with pg_factory.begin() as session:
        with pytest.raises(RuntimeError, match="restore a backup"):
            await migrate(session, "5f3ad79d9033", down=True)
    async with pg_factory() as session:
        assert len((await session.scalars(select(Question))).all()) == 10


@pytest.mark.parametrize("conflict", ["duplicate_art", "unapproved_interest"])
async def test_interest_conflict_rolls_back_catalogue_upgrade(pg_factory, conflict):
    async with pg_factory.begin() as session:
        await migrate(session, "5f3ad79d9033", down=True)
        if conflict == "duplicate_art":
            await session.execute(
                text("""
                INSERT INTO interests(name,is_approved)
                VALUES('Искусство',true),('Культура и искусство',true)
            """)
            )
        else:
            await session.execute(
                text("INSERT INTO interests(name,is_approved) VALUES('Спорт',false)")
            )
    with pytest.raises(RuntimeError, match="Unexpected"):
        async with pg_factory.begin() as session:
            await migrate(session, "head")
    async with pg_factory() as session:
        assert (await session.execute(text("SELECT count(*) FROM questions"))).scalar_one() == 0


async def test_downgrade_preserves_legacy_question_changed_after_upgrade(pg_factory):
    async with pg_factory.begin() as session:
        await migrate(session, "5f3ad79d9033", down=True)
        legacy_id = (
            await session.execute(
                text("""
            INSERT INTO questions(text,type,"order",is_active)
            VALUES('Какие ещё увлечения вы хотите указать?','text',3,false) RETURNING id
        """)
            )
        ).scalar_one()
        await migrate(session, "head")
        await session.execute(
            text("UPDATE questions SET is_active=true WHERE id=:id"), {"id": legacy_id}
        )
    async with pg_factory.begin() as session:
        with pytest.raises(RuntimeError, match="restore a backup"):
            await migrate(session, "91d04c247b51", down=True)
    async with pg_factory() as session:
        assert (
            await session.execute(
                text("SELECT is_active FROM questions WHERE id=:id"), {"id": legacy_id}
            )
        ).scalar_one() is True
