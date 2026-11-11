"""Survey v2 catalogue. Existing data changes require an explicit migration.

The data migration contains its own frozen copy; never import this module there.
"""

type SeedOption = tuple[str, tuple[str, str] | None]
type SeedQuestion = tuple[str, str, str, int, bool, tuple[str, str] | None, list[SeedOption]]

OFFICES = [("Тестовый город", "Демонстрационный офис")]
INTERESTS = {
    "Спорт": ["Волейбол", "Баскетбол", "Бег", "Футбол"],
    "Культура и искусство": ["Рисование", "Театр", "Музыка", "Фотография", "Кино"],
    "Экология": [],
    "Животные": [],
    "Отдых": ["Клуб", "Шашлык", "Бар", "Прогулки"],
    "Настольные игры": [],
    "Технологии": [],
}
# code, text, type, stage, required, trigger (question code, option label), options
QUESTIONS: list[SeedQuestion] = [
    (
        "v2_directions",
        "Что тебе интересно?",
        "multi",
        1,
        True,
        None,
        [(label, None) for label in [*INTERESTS, "Другое"]],
    ),
    (
        "v2_sport",
        "Какой спорт тебе интересен?",
        "multi",
        2,
        True,
        ("v2_directions", "Спорт"),
        [(label, ("Спорт", label)) for label in INTERESTS["Спорт"]] + [("Другое", None)],
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
        [(label, ("Культура и искусство", label)) for label in INTERESTS["Культура и искусство"]]
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
        [(label, ("Отдых", label)) for label in INTERESTS["Отдых"]] + [("Другое", None)],
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
