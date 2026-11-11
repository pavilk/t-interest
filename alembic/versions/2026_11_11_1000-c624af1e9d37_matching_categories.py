"""Link the standard survey direction options to matching categories."""

import sqlalchemy as sa

from alembic import op

revision = "c624af1e9d37"
down_revision = "b762e95c1a83"
branch_labels = None
depends_on = None

CATEGORIES = (
    "Спорт",
    "Культура и искусство",
    "Экология",
    "Животные",
    "Отдых",
    "Настольные игры",
    "Технологии",
)


def upgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(87134019)")
    op.add_column("answer_options", sa.Column("category_interest_id", sa.Integer()))
    op.create_foreign_key(
        op.f("fk_answer_options_category_interest_id_interests"),
        "answer_options",
        "interests",
        ["category_interest_id"],
        ["id"],
    )
    statement = sa.text("""
        UPDATE answer_options AS option
        SET category_interest_id = category.id
        FROM questions AS question, interests AS category
        WHERE option.question_id = question.id
          AND question.code = 'v2_directions'
          AND option.text IN :categories
          AND category.parent_id IS NULL
          AND lower(category.name) = lower(option.text)
        RETURNING option.text
    """).bindparams(sa.bindparam("categories", expanding=True))
    linked = op.get_bind().execute(statement, {"categories": CATEGORIES}).scalars().all()
    if sorted(linked) != sorted(CATEGORIES):
        raise RuntimeError("Expected one option and root interest for each standard category")


def downgrade() -> None:
    op.drop_column("answer_options", "category_interest_id")
