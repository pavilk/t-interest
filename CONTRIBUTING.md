# Как дописывать сервисы и репозитории

Готовый образец — модуль **identity** (регистрация, вход, профиль). Все остальные модули делаются по нему.
Правила проекта — в [CLAUDE.md](CLAUDE.md), контракты эндпоинтов и бизнес-правила — в
[ARCHITECTURE.md](.claude/plans/backend-architecture/ARCHITECTURE.md).

## Запуск

```bash
py -3.14 -m venv .venv            # или python3.14 -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest                            # unit-тесты, база не нужна
ruff check . && ruff format .
mypy
```

## Что уже есть

| Файл | Что внутри |
|---|---|
| `app/core/config.py` | настройки из `.env` |
| `app/core/db.py` | `session_scope` / `get_session`: одна сессия на запрос (в WS — на фрейм), **единственное место с `commit`**; outbox событий, которые уходят только после commit |
| `app/core/errors.py` | доменные ошибки: `NotFoundError`, `ForbiddenError`, `ConflictError`, `DomainValidationError`, `UnauthorizedError`, `RateLimitedError` |
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
| **chat** — готов | `repositories/chat.py`, `services/chat.py`, `api/chats.py` (REST), `api/ws.py` + `api/connections.py` (WebSocket). Для matching и groups: `ChatService.ensure_direct_chat`, `create_group_chat`, `add_group_member`, `remove_group_member` в той же сессии запроса |
| `tests/repositories/` | тесты на настоящей PostgreSQL, нужен `TEST_DATABASE_URL` (см. CLAUDE.md) |

Миграции — в `alembic/` (команды в CLAUDE.md). Документация ручек: запусти
`uvicorn app.main:app --reload` и открой http://localhost:8000/docs. Фреймы WebSocket
описаны в `/openapi.json` как `ClientFrame` и `ServerFrame`.

## Как добавить модуль: 7 шагов

На примере **survey**. Открой рядом соответствующий файл identity и делай так же.

**1. Интерфейс** в `app/repositories/interfaces.py`:

```python
class SurveyRepository(Protocol):
    async def list_active_questions(self) -> Sequence[Question]: ...
    async def list_options(self, question_ids: Sequence[int]) -> Sequence[AnswerOption]: ...
    async def replace_answers(self, user_id: int, answers: Sequence[UserAnswer]) -> None: ...
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
| **survey** | `SurveyRepository`: активные вопросы, варианты, замена ответов. `InterestRepository`: дерево интересов, замена интересов пользователя | `SurveyService`: `get_survey`, `get_answers`, `submit_answers` (валидация single/multi/text, обязательные вопросы, пересчёт `user_interests` из `answer_options.interest_id`, `survey_completed = True`), `get_matching_profile(user_ids)` | Contracts → survey |
| **matching** | `MatchRepository`: `get_pair(a, b)`, `add`, `excluded_user_ids(user_id)` | `services/matching.py`: **чистые функции** `jaccard`, `score` + `MatchingService`: `recommend_people`, `like`, `decline`. Пара всегда `user_a_id < user_b_id` | Contracts → matching, Decisions 4–5 |
| **groups** | `GroupRepository`: `get`, `get_active_by_interest`, `add`, `list_pending`, `add_member`, `is_member`, `recommend_for_interests` | `GroupService`: `create_request`, `approve`/`reject` (только moderator/admin), `join`, `leave` | Contracts → groups |
| **search** | `SearchRepository`: `ILIKE` по людям и одобренным группам | `SearchService`: строка ≥ 2 символов, limit 20 | Data flow → 6 |
