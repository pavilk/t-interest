# Как дописывать сервисы и репозитории

Готовый образец — модуль **identity** (регистрация, вход, профиль). Все остальные модули делаются по нему.
Правила проекта — в [CLAUDE.md](CLAUDE.md), контракты эндпоинтов и бизнес-правила — в
[ARCHITECTURE.md](.claude/plans/backend-architecture/ARCHITECTURE.md).

## Запуск

```bash
py -3.14 -m venv .venv            # или python3.14 -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest                            # unit/API-тесты на фейках, база не нужна
pytest -m postgres                # интеграционные тесты, нужен TEST_DATABASE_URL
ruff check . && ruff format .
mypy
```

## Что уже есть

| Файл | Что внутри |
|---|---|
| `app/core/config.py` | настройки из `.env` |
| `app/core/db.py` | `get_session`: одна сессия на запрос, **единственное место с `commit`** |
| `app/core/errors.py` | доменные ошибки: `NotFoundError`, `ForbiddenError`, `ConflictError`, `DomainValidationError`, `UnauthorizedError` |
| `app/core/security.py` | хэш паролей (argon2), JWT |
| `app/models/` | **все** таблицы проекта, менять только вместе с CLAUDE.md |
| `app/repositories/interfaces.py` | интерфейсы репозиториев (Protocol) |
| `app/repositories/user.py`, `office.py` | образец репозитория |
| `app/services/identity.py` | образец сервиса |
| `app/schemas/` | Pydantic-схемы запросов и ответов; `errors.py` — формат ошибки |
| `app/core/deps.py` | DI: сборка репозиториев и сервисов, `CurrentUser`, `SurveyCompletedUser` |
| `app/api/auth.py`, `app/api/me.py` | **образец ручек**: `/auth/register`, `/auth/login`, `/auth/refresh`, `/me`, `/offices` |
| `app/api/errors.py` | доменные ошибки → HTTP-коды и JSON `{"error": {"code", "message"}}` |
| `app/main.py` | приложение, подключение роутеров под `/api/v1` |
| `tests/fakes/repositories.py` | фейковые репозитории в памяти |
| `tests/unit/test_identity_service.py` | образец тестов сервиса |
| `tests/api/test_identity_api.py` | образец тестов ручек (на фейках, без БД) |

Миграции Alembic, справочники и модуль survey реализованы. Документация ручек:
запусти `uvicorn app.main:app --reload` и открой http://localhost:8000/docs.

## База данных и анкета

Скопируй `.env.example` в `.env`, задай `JWT_SECRET` и согласованные
`POSTGRES_PASSWORD`/`DATABASE_URL`. Затем:

```bash
docker compose up -d --wait postgres
alembic upgrade head
python -m seeds
uvicorn app.main:app --reload
```

Compose пока поднимает PostgreSQL; backend запускается локально. Начальная миграция
создаёт все уже существовавшие модели проекта. Повторный `python -m seeds` сохраняет
идентификаторы, ответы и ручную деактивацию вопросов. Сиды — демонстрационный черновик:
один вымышленный офис, два направления, четыре поднаправления, вопросы single/multi/text.
Пользовательские аккаунты не создаются. Итоговые формулировки согласует команда.

Все пути ниже имеют префикс `/api/v1` и требуют access JWT; анкета доступна до завершения
онбординга:

| Ручка | Назначение |
|---|---|
| `GET /survey` | Активные вопросы по `(order, id)` с вариантами |
| `GET /survey/answers` | Свои ответы на активные вопросы |
| `PUT /survey/answers` | Полная замена активных ответов; возвращает `MeOut` |
| `GET /interests` | Одобренное дерево направлений и поднаправлений |

Пример тела PUT (ID нужно взять из GET, номера не фиксированы):

```json
[
  {"question_id": 1, "option_ids": [1]},
  {"question_id": 2, "option_ids": [3, 4]},
  {"question_id": 3, "text_value": "Другие увлечения"}
]
```

Все активные single/multi обязательны. Single принимает один вариант, multi — минимум
один; дубликаты запрещены. Text необязателен: для удаления прежнего текста пропусти
вопрос. Если текст передан, после strip он должен быть непустым; максимум 300 символов
до strip. «Другое» представлено отдельным текстовым вопросом. user_id задаётся JWT,
лишние поля входа запрещены.

Ответы отключённых вопросов остаются в БД, но скрыты в текущей выдаче. PUT пересчитывает
интересы только по активным ответам; выбранные варианты должны ссылаться на одобренные
поднаправления. Ответы, интересы и `survey_completed` меняются атомарно; два PUT одного
пользователя сериализуются блокировкой строки users. Неудачный PUT сохраняет прежнее состояние.

Коды 422: `unknown_question`, `option_not_in_question`, `single_choice_violation`,
`multi_choice_violation`, `required_question_missing`, `duplicate_question`, `duplicate_option`,
`invalid_answer_type`, `empty_text`, `text_too_long`; ошибки структуры — `validation_error`.
Без активных обязательных вопросов или при выбранной некорректной ссылке на интерес —
409 `survey_not_configured`. Формат ошибок общий: `{"error": {"code", "message"}}`.

При изменении текста вопроса, типа или связи варианта с интересом используй явную миграцию.
Ключи сидов: интерес — имя без учёта регистра/родитель; вопрос — текст; вариант — вопрос/текст.
Редактирование этих ключей в seeds/data.py добавит новые записи. Изменение активности или
связей не пересчитывает уже сохранённые user_interests автоматически: такая операция
должна включать пересчёт по активным ответам. Автоматического сброса survey_completed нет.

## Проверки на PostgreSQL

```bash
docker compose --profile test up -d --wait postgres-test
```

Задай `TEST_DATABASE_URL` как переменную окружения (pytest намеренно не берёт её из `.env`):

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://tinterest_test:tinterest_test@localhost:5433/tinterest_test'
pytest -m postgres
```

Linux/macOS: `export TEST_DATABASE_URL=postgresql+asyncpg://tinterest_test:tinterest_test@localhost:5433/tinterest_test`.
Имя тестовой БД должно содержать `test`; она должна отличаться от рабочей. Каждый тест
создаёт и удаляет только собственную случайную схему, применяя настоящие миграции.
Проверяются ограничения PostgreSQL, round-trip миграций, два конкурентных PUT, ошибки
после записи ответов и при commit, сквозное заполнение анкеты и повтор сидов. Явный запуск
`pytest -m postgres` без URL завершается ошибкой; пропуск не считается успешной проверкой.

## Как добавить модуль: 7 шагов

На примере **survey**. Открой рядом соответствующий файл identity и делай так же.

**1. Интерфейс** в `app/repositories/interfaces.py`:

```python
class SurveyRepository(Protocol):
    async def list_active_questions(self) -> Sequence[Question]: ...
    async def list_options(self, question_ids: Sequence[int]) -> Sequence[AnswerOption]: ...
    async def replace_active_answers(
        self, user_id: int, question_ids: Sequence[int], answers: Sequence[UserAnswer]
    ) -> None: ...
```

Метод называй по смыслу для сервиса, а не по SQL. Возвращай модели или простые значения, не схемы.

**2. Реализация** — `app/repositories/survey.py`, класс `SqlSurveyRepository(session)`:
- все запросы SQLAlchemy только здесь;
- **никогда не вызывай `commit`**; `flush` — только если после `add` нужен id;
- `delete` + `add_all` для полной замены ответов — нормально, это всё одна транзакция.

**3. Схемы** — `app/schemas/survey.py`. Ограничения длины и формата — через `Field(...)`, чтобы
их проверял Pydantic, а не сервис.

**4. Сервис** — `app/services/survey.py`, класс `SurveyService`:
- в конструктор приходят **интерфейсы** репозиториев (и `Settings`, если нужен);
- никаких импортов из `sqlalchemy` — если захотелось, значит запрос должен уехать в репозиторий;
- бизнес-ошибки — только через `app/core/errors.py`, с `code` из контрактов ARCHITECTURE.md
  (`single_choice_violation`, `required_question_missing` и т.д.);
- чтобы изменить объект, просто меняй его поля: изменения сохранит `commit` в `get_session`;
- сервис может вызывать другой сервис (например matching → `ChatService.ensure_direct_chat`),
  но не чужой репозиторий.

**5. Тесты** — `tests/unit/test_survey_service.py`:
- добавь фейк в `tests/fakes/repositories.py` с теми же методами, что в Protocol;
- один тест на каждое правило и каждый код ошибки; ошибки проверяй по `exc.value.code`;
- тесты должны проходить без базы.

**6. DI** — в `app/core/deps.py`:

```python
def get_survey_repository(session: SessionDep) -> SurveyRepository:
    return SqlSurveyRepository(session)

def get_survey_service(surveys: SurveyRepositoryDep, ...) -> SurveyService:
    return SurveyService(surveys, ...)

SurveyServiceDep = Annotated[SurveyService, Depends(get_survey_service)]
```

Сессию бери только через `SessionDep`: там `scope="function"`, иначе commit случится после ответа.

**7. Ручки** — `app/api/survey.py` по образцу `app/api/me.py`, подключить в `app/main.py`:
- роутер только принимает схему, зовёт **один** метод сервиса и возвращает результат;
- пользователь — через `CurrentUser`, а для ручек после анкеты — `SurveyCompletedUser`;
- коды ошибок перечисли в `responses=error_responses(...)`, чтобы они попали в OpenAPI;
- тесты в `tests/api/` по образцу `test_identity_api.py`: подмени свои `get_*_repository`
  в `tests/api/conftest.py` и проверь коды ответов и авторизацию. Бизнес-правила уже
  проверены в unit-тестах, их не дублируй.

Перед пушем: `pytest`, `ruff check .`, `ruff format .`, `mypy` — всё зелёное.

## Чек-лист ревью

- [ ] В `services/` нет `import sqlalchemy` и нет `commit`.
- [ ] В `repositories/` нет `commit` и бизнес-правил (проверок «можно ли»).
- [ ] В `api/` нет логики и обращений к репозиториям: только вызов сервиса.
- [ ] Каждая ручка, кроме регистрации и входа, требует `CurrentUser` или `SurveyCompletedUser`.
- [ ] Каждый метод из Protocol есть и в Sql-реализации, и в фейке.
- [ ] Коды ошибок совпадают с ARCHITECTURE.md.
- [ ] Нет логов с почтой, именами и текстом сообщений.
- [ ] Тестовые данные вымышленные.

## Что осталось сделать

Порядок — по приоритетам команды. Методы ниже — подсказка, итоговый набор выводи из контрактов.

| Модуль | Репозиторий | Сервис и главные правила | Раздел ARCHITECTURE.md |
|---|---|---|---|
| **survey — готов** | `SurveyRepository`: активные вопросы, варианты, замена активных ответов с сохранением истории. `InterestRepository`: дерево интересов, замена интересов пользователя | `SurveyService`: `get_survey`, `get_answers`, `submit_answers`, `list_interests`, `get_matching_profile(user_ids)`; модели и Protocol — образец для следующих модулей | Contracts → survey |
| **chat** | `ChatRepository`: `get_direct_by_key`, `add_chat`, `add_member`, `is_member`, `list_user_chats`, `add_message`, `list_messages(before_id, limit)`, `mark_read` | `ChatService`: `ensure_direct_chat(a, b)` (ключ `"<min>:<max>"`, идемпотентно), `create_group_chat`, `send_message` (проверка членства, текст 1..4000), `list_chats` с unread | Contracts → chat |
| **matching** | `MatchRepository`: `get_pair(a, b)`, `add`, `excluded_user_ids(user_id)` | `services/matching.py`: **чистые функции** `jaccard`, `score` + `MatchingService`: `recommend_people`, `like`, `decline`. Пара всегда `user_a_id < user_b_id` | Contracts → matching, Decisions 4–5 |
| **groups** | `GroupRepository`: `get`, `get_active_by_interest`, `add`, `list_pending`, `add_member`, `is_member`, `recommend_for_interests` | `GroupService`: `create_request`, `approve`/`reject` (только moderator/admin), `join`, `leave` | Contracts → groups |
| **search** | `SearchRepository`: `ILIKE` по людям и одобренным группам | `SearchService`: строка ≥ 2 символов, limit 20 | Data flow → 6 |
