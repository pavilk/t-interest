# Planner Context — t-interest

> Проект-специфичный контекст для skill'а `planner` (плагин `planner` из
> marketplace `i-m-senior-developer`). Планнер читает этот файл перед
> построением плана.
>
> Формат: таблицы. Допустимо редактировать вручную — планнер не
> перезаписывает ручные правки, только дополняет новыми автосканированными
> строками с меткой `<!-- auto-added YYYY-MM-DD -->` и помечает исчезнувшие
> элементы `<!-- stale, last seen YYYY-MM-DD -->`. Уроки из `/plan-reflect`
> приходят с меткой `<!-- learned YYYY-MM-DD -->`.

## 1. Каталог агентов

TODO: fill manually — проектных и глобальных агентов нет.

| Имя (как звать) | Источник | Роль | Сильные стороны | Когда звать |
|---|---|---|---|---|
| planner:planner | plugin | Строит архитектуру или план выполнения в файл | Файловые планы с версиями | Перед реализацией |
| planner:product-baseline | plugin | Встроенный ограниченный продуктовый поставщик | Черновик продуктовой консультации | Только по маршрутизации product-discovery |
| general-purpose | builtin | Универсальный агент | Поиск, многошаговые задачи | Fallback |
| Explore | builtin | Поиск по коду (read-only) | Широкий поиск | Когда нужен вывод, а не файлы |
| Plan | builtin | Архитектор (read-only) | План реализации | Проектирование |
| ❌ GAP (backend-python, FastAPI) | — | Реализация бэкенда FastAPI | — | fallback: general-purpose |
| ❌ GAP (infra, docker-compose) | — | Docker Compose / CI | — | fallback: general-purpose |

## 2. Каталог slash-команд

TODO: fill manually — проектных и глобальных команд нет.

| Команда | Источник | Назначение |
|---|---|---|

## 3. Каталог skills

TODO: fill manually — проектных и глобальных skills нет.

| Skill | Источник | Триггер активации |
|---|---|---|
| planner:planner | plugin | Архитектура или план выполнения в файл |
| planner:plan-do | plugin | Выполнение плана с ревью |
| planner:plan-reflect | plugin | Рефлексия после выполнения плана |
| planner:product-discovery | plugin | Сырой продуктовый замысел |
| planner:plan-feat | plugin | Требования к одной фиче |
| code-review | builtin | Ревью диффа |
| security-review | builtin | Security-ревью изменений |

## 4. Таблица моделей (default, переопределяй если нужно)

| Модель | Сила | Слабость | $/time | Применять для |
|---|---|---|---|---|
| Opus 5 | Глубокие рассуждения, сложная архитектура, нестандартные алгоритмы | Дорого, медленно | ≈5× Sonnet | Security-core, новая незнакомая область, спорные ADR |
| Sonnet 5 | Баланс: контекст + надёжный код | Теряется в очень сложных цепочках | baseline | 80% задач: CRUD, компоненты, миграции, обычная архитектура |
| Haiku 4.5 | Быстрый, дешёвый | Слабее на нюансах | ≈0.2× Sonnet | Тривиальные правки, форматирование, проверка импортов |

## 5. Хранение артефактов фич

- **Корень фич:** `features/` (пока не создан)
- **Корень замыслов:** `ideas/`
- **Корень общих гипотез:** `epics/`
- **Паттерн имени:** `FEAT-XXXX-<slug>/`
- **Артефакты внутри фичи:**
  - `README.md` — требования
  - `ARCHITECTURE.md` — готовая архитектура с версией и отпечатком тела
  - `PLANNER_EXECUTION.md` — план выполнения со ссылкой на архитектуру
  - `PLANNER_OUTPUT.md` — сохранённый legacy-артефакт; новые запуски его не используют
  - `review-request-changes/REVIEW-NN.md` — отчёт ревью за один раунд
  - `screenshots/`, `test_cases/`
- **Свободные задачи:** `.claude/plans/<task-slug>/`
- **Контекстные файлы проекта:**
  - `CLAUDE.md` — обзор проекта, слои, сущности, безопасность

## 6. Соглашения именования

- Репозиторий только бэкенда; веб — отдельный репозиторий.
- Слои `api → services → repositories → models`; один репозиторий на агрегат (`UserRepository`, `SurveyRepository`, …).
- Документация и планы — на русском.

## 7. Метаданные bootstrap

- Последний auto-scan: 2026-10-13
- Количество найденных агентов: 5 (project: 0, global: 0, plugin: 2, builtin: 3)
- Количество найденных skills: 7 релевантных (project: 0, global: 0)
- Количество найденных команд: 0

## 8. Unknown markers

Нет.

## §9 Способности и поставщики

| Способность | Нужна для | Поставщик | Источник | Доступность | Покрытие | Основание | Ограничения | Приоритет |
|---|---|---|---|---|---|---|---|---|
| problem_outcome_framing | idea, epic, feature | planner:product-baseline | builtin | available | partial | plugins/planner/skills/product-baseline/SKILL.md | нет данных о пользователях; ограниченный режим | builtin |
| product_synthesis | idea, epic, roadmap | planner:product-baseline | builtin | available | partial | plugins/planner/skills/product-baseline/SKILL.md | нет данных о пользователях; ограниченный режим | builtin |
| decision_dialogue | idea, epic, roadmap, feature | planner:product-baseline | builtin | available | partial | plugins/planner/skills/product-baseline/SKILL.md | нет независимой многоролевой проверки; ограниченный режим | builtin |

Машинный раздел: его разбирает помощник `product_state.py` скилла
`product-discovery` (подкоманда `parse-capabilities`); правила выбора
поставщика и остановки — в `product-discovery/references/routing.md`.
Заголовок таблицы и порядок девяти колонок — дословно, менять нельзя; первая
непустая строка после заголовка раздела обязана быть заголовком таблицы,
проза допускается только после пустой строки за таблицей. Закрытые значения,
дословно: Доступность — `available`, `stale`, `error`, `not-surfaced`;
Покрытие — `full`, `partial`, `unknown`, `none`; Приоритет — `configured`,
`project`, `plugin`, `builtin`. «Нужна для» — виды документов через запятую:
`idea`, `epic`, `roadmap`, `feature`. «Основание» — конкретный путь или
запись обнаружения; пустое основание принудительно переводит покрытие в
`unknown`. Совпадение имени поставщика со способностью основанием не
является. Новые строки помечаются датой обнаружения, исчезнувшие не
удаляются, а помечаются; метки `<!-- ... -->` пишутся внутри ячейки
«Основание» — отдельная строка внутри таблицы ломает машинный разбор.
