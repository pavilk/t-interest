"""Conditional survey metadata and configuration rollback journal.

Revision ID: 91d04c247b51
Revises: 5f3ad79d9033
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "91d04c247b51"
down_revision = "5f3ad79d9033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("code", sa.String(100), nullable=True))
    op.add_column("questions", sa.Column("stage", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("questions", sa.Column("is_required", sa.Boolean(), nullable=True))
    op.add_column("questions", sa.Column("show_if_option_id", sa.Integer(), nullable=True))
    op.execute("UPDATE questions SET code = 'legacy_' || id, is_required = (type <> 'text')")
    op.alter_column("questions", "code", nullable=False)
    op.alter_column("questions", "is_required", nullable=False)
    op.create_unique_constraint(op.f("uq_questions_code"), "questions", ["code"])
    op.create_check_constraint(
        op.f("ck_questions_valid_stage"), "questions", "stage BETWEEN 1 AND 3"
    )
    op.create_foreign_key(
        "fk_questions_show_if_option_id_answer_options",
        "questions",
        "answer_options",
        ["show_if_option_id"],
        ["id"],
    )
    op.create_table(
        "survey_migration_state",
        sa.Column("revision", sa.String(32), primary_key=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("survey_migration_state")
    op.drop_constraint(
        "fk_questions_show_if_option_id_answer_options", "questions", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_questions_valid_stage"), "questions", type_="check")
    op.drop_constraint(op.f("uq_questions_code"), "questions", type_="unique")
    for column in ("show_if_option_id", "is_required", "stage", "code"):
        op.drop_column("questions", column)
