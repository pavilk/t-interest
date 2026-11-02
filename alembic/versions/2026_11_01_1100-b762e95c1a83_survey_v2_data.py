"""Frozen survey v2 catalogue; independent of seeds and application code.

Revision ID: b762e95c1a83
Revises: 91d04c247b51
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "b762e95c1a83"
down_revision = "91d04c247b51"
branch_labels = None
depends_on = None

LEGACY = {
    "Какой формат знакомства вам удобнее?": "single",
    "Какие занятия вам интересны?": "multi",
    "Какие ещё увлечения вы хотите указать?": "text",
}
DIRECTIONS = {
    "Спорт": ["Волейбол", "Баскетбол", "Бег", "Футбол"],
    "Культура и искусство": ["Рисование", "Театр", "Музыка", "Фотография", "Кино"],
    "Экология": [],
    "Животные": [],
    "Отдых": ["Клуб", "Шашлык", "Бар", "Прогулки"],
    "Настольные игры": [],
    "Технологии": [],
}
# code, text, type, stage, required, trigger (question code, option label), options
CATALOGUE = [
    (
        "v2_directions",
        "Что тебе интересно?",
        "multi",
        1,
        True,
        None,
        [(label, None) for label in [*DIRECTIONS, "Другое"]],
    ),
    (
        "v2_sport",
        "Какой спорт тебе интересен?",
        "multi",
        2,
        True,
        ("v2_directions", "Спорт"),
        [(label, ("Спорт", label)) for label in DIRECTIONS["Спорт"]] + [("Другое", None)],
    ),
    (
        "v2_sport_other",
        "Какой другой спорт тебе интересен?",
        "text",
        2,
        True,
        ("v2_sport", "Другое"),
        [],
    ),
    (
        "v2_art",
        "Какое искусство тебе ближе?",
        "multi",
        2,
        True,
        ("v2_directions", "Культура и искусство"),
        [(label, ("Культура и искусство", label)) for label in DIRECTIONS["Культура и искусство"]]
        + [("Другое", None)],
    ),
    (
        "v2_art_other",
        "Какое другое искусство тебе ближе?",
        "text",
        2,
        True,
        ("v2_art", "Другое"),
        [],
    ),
    (
        "v2_rest",
        "Что ты любишь в отдыхе?",
        "multi",
        2,
        True,
        ("v2_directions", "Отдых"),
        [(label, ("Отдых", label)) for label in DIRECTIONS["Отдых"]] + [("Другое", None)],
    ),
    (
        "v2_rest_other",
        "Какой другой отдых тебе нравится?",
        "text",
        2,
        True,
        ("v2_rest", "Другое"),
        [],
    ),
    ("v2_animal", "Какое у тебя животное?", "text", 2, False, ("v2_directions", "Животные"), []),
    ("v2_other", "Что тебе интересно?", "text", 2, True, ("v2_directions", "Другое"), []),
    ("v2_about", "Расскажи о себе", "text", 3, False, None, []),
]


def _rows(connection, table, ids):
    if not ids:
        return []
    return [
        dict(row)
        for row in connection.execute(
            sa.text(f"SELECT * FROM {table} WHERE id IN :ids ORDER BY id").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        ).mappings()
    ]


def _recalculate(connection):
    connection.execute(sa.text("DELETE FROM user_interests"))
    connection.execute(
        sa.text("""
        INSERT INTO user_interests(user_id, interest_id)
        SELECT DISTINCT a.user_id, leaf.id
        FROM user_answers a
        JOIN questions q ON q.id=a.question_id AND q.is_active
        JOIN answer_options o ON o.id=a.option_id AND o.question_id=q.id
        JOIN interests leaf ON leaf.id=o.interest_id AND leaf.is_approved
        JOIN interests root ON root.id=leaf.parent_id AND root.parent_id IS NULL
                           AND root.is_approved
    """)
    )


def upgrade() -> None:
    connection = op.get_bind()
    # Cooperates with seed commands; block catalogue/answer writes for atomic rollout.
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(87134019)"))
    connection.execute(
        sa.text(
            "LOCK TABLE questions, answer_options, interests, users, user_answers, user_interests "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    previous = list(connection.execute(sa.text("SELECT * FROM questions ORDER BY id")).mappings())
    known = [q for q in previous if q["text"] in LEGACY]
    if (
        any(q["is_active"] and q["text"] not in LEGACY for q in previous)
        or len({q["text"] for q in known}) != len(known)
        or any(q["type"] != LEGACY[q["text"]] for q in known)
    ):
        raise RuntimeError("Unexpected survey configuration; review before upgrading")
    snapshot = {
        "previous_questions": [{"id": q["id"], "is_active": q["is_active"]} for q in known],
        "renamed_interests": [],
        "created_interests": [],
        "created_questions": [],
        "created_options": [],
    }
    old_art = (
        connection.execute(
            sa.text(
                "SELECT id, name FROM interests WHERE parent_id IS NULL "
                "AND lower(name)=lower(:name)"
            ),
            {"name": "Искусство"},
        )
        .mappings()
        .first()
    )
    if old_art:
        target = connection.execute(
            sa.text(
                "SELECT id FROM interests WHERE parent_id IS NULL AND lower(name)=lower(:name)"
            ),
            {"name": "Культура и искусство"},
        ).first()
        if target:
            raise RuntimeError("Unexpected duplicate art directions; review before upgrading")
        snapshot["renamed_interests"].append(dict(old_art))
        connection.execute(
            sa.text("UPDATE interests SET name=:name WHERE id=:id"),
            {"name": "Культура и искусство", "id": old_art["id"]},
        )

    def interest(name, parent):
        row = (
            connection.execute(
                sa.text(
                    "SELECT id,is_approved FROM interests WHERE lower(name)=lower(:name) "
                    "AND parent_id IS NOT DISTINCT FROM :parent"
                ),
                {"name": name, "parent": parent},
            )
            .mappings()
            .first()
        )
        if row:
            if not row["is_approved"]:
                raise RuntimeError("Unexpected unapproved catalogue interest")
            return row["id"]
        result = connection.execute(
            sa.text(
                "INSERT INTO interests(name,parent_id,is_approved) "
                "VALUES(:name,:parent,true) RETURNING id"
            ),
            {"name": name, "parent": parent},
        ).scalar_one()
        snapshot["created_interests"].append(result)
        return result

    interest_ids = {}
    for direction, children in DIRECTIONS.items():
        parent = interest(direction, None)
        for child in children:
            interest_ids[(direction, child)] = interest(child, parent)
    option_ids = {}
    for order, (code, label, kind, stage, required, trigger, options) in enumerate(CATALOGUE, 1):
        question_id = connection.execute(
            sa.text("""
            INSERT INTO questions
                (code,text,type,"order",is_active,stage,is_required,show_if_option_id)
            VALUES
                (:code,:label,CAST(:kind AS question_type),:ordering,true,:stage,:required,:trigger)
            RETURNING id
        """),
            {
                "code": code,
                "label": label,
                "kind": kind,
                "ordering": order,
                "stage": stage,
                "required": required,
                "trigger": option_ids[trigger] if trigger else None,
            },
        ).scalar_one()
        snapshot["created_questions"].append(question_id)
        for option_order, (text, leaf) in enumerate(options, 1):
            option_id = connection.execute(
                sa.text("""
                INSERT INTO answer_options(question_id,text,"order",interest_id)
                VALUES(:question,:text,:ordering,:interest) RETURNING id
            """),
                {
                    "question": question_id,
                    "text": text,
                    "ordering": option_order,
                    "interest": interest_ids[leaf] if leaf else None,
                },
            ).scalar_one()
            option_ids[(code, text)] = option_id
            snapshot["created_options"].append(option_id)
    for row in known:
        connection.execute(
            sa.text("UPDATE questions SET is_active=false WHERE id=:id"), {"id": row["id"]}
        )
    # Store only catalogue configuration, never users, their answers, or message contents.
    snapshot["questions_after"] = _rows(
        connection, "questions", snapshot["created_questions"] + [q["id"] for q in known]
    )
    snapshot["options_after"] = _rows(connection, "answer_options", snapshot["created_options"])
    snapshot["interests_after"] = _rows(
        connection,
        "interests",
        snapshot["created_interests"] + [i["id"] for i in snapshot["renamed_interests"]],
    )
    connection.execute(
        sa.text(
            "INSERT INTO survey_migration_state(revision,snapshot) "
            "VALUES(:revision,CAST(:state AS jsonb))"
        ),
        {"revision": revision, "state": json.dumps(snapshot, ensure_ascii=False)},
    )
    _recalculate(connection)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(87134019)"))
    connection.execute(
        sa.text(
            "LOCK TABLE questions, answer_options, interests, users, user_answers, user_interests, "
            "groups IN ACCESS EXCLUSIVE MODE"
        )
    )
    snapshot = connection.execute(
        sa.text("SELECT snapshot FROM survey_migration_state WHERE revision=:revision"),
        {"revision": revision},
    ).scalar_one()
    q_ids, o_ids, i_ids = (
        snapshot[k] for k in ("created_questions", "created_options", "created_interests")
    )

    def unsafe():
        raise RuntimeError(
            "Survey v2 data is in use or changed; restore a backup instead of downgrade"
        )

    for table, ids, expected in (
        (
            "questions",
            q_ids + [q["id"] for q in snapshot["previous_questions"]],
            snapshot["questions_after"],
        ),
        ("answer_options", o_ids, snapshot["options_after"]),
        (
            "interests",
            i_ids + [i["id"] for i in snapshot["renamed_interests"]],
            snapshot["interests_after"],
        ),
    ):
        if _rows(connection, table, ids) != expected:
            unsafe()
    # Inspect every FK referencing rows to remove, including future dependent tables.
    # References within the catalogue itself are removed together and are safe.
    removal = {"questions": q_ids, "answer_options": o_ids, "interests": i_ids}
    inspector = sa.inspect(connection)
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            ids = removal.get(fk["referred_table"])
            if not ids or fk["referred_columns"] != ["id"]:
                continue
            column = fk["constrained_columns"][0]
            query = f'SELECT 1 FROM "{table}" WHERE "{column}" IN :ids'
            params = {"ids": ids}
            bindings = [sa.bindparam("ids", expanding=True)]
            if table in removal:
                query += " AND id NOT IN :own"
                params["own"] = removal[table]
                bindings.append(sa.bindparam("own", expanding=True))
            if connection.execute(
                sa.text(query + " LIMIT 1").bindparams(*bindings), params
            ).first():
                unsafe()
    connection.execute(
        sa.text("UPDATE questions SET show_if_option_id=NULL WHERE id IN :ids").bindparams(
            sa.bindparam("ids", expanding=True)
        ),
        {"ids": q_ids},
    )
    for table, ids in (("answer_options", o_ids), ("questions", q_ids), ("interests", i_ids)):
        if ids:
            connection.execute(
                sa.text(f"DELETE FROM {table} WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
    for row in snapshot["previous_questions"]:
        connection.execute(sa.text("UPDATE questions SET is_active=:is_active WHERE id=:id"), row)
    for row in snapshot["renamed_interests"]:
        connection.execute(sa.text("UPDATE interests SET name=:name WHERE id=:id"), row)
    _recalculate(connection)
    connection.execute(
        sa.text("DELETE FROM survey_migration_state WHERE revision=:revision"),
        {"revision": revision},
    )
