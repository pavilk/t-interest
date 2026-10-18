---
plan_type: execution
version: 2
status: current
content_sha256: e8ccdfe20026366ea910dd28c7df0ed74d471f66f47202f5bd138cf9d02936da
architecture:
  path: "./ARCHITECTURE.md"
  version: 2
  content_sha256: 955504574c45402b82e404eafca7fe5cca097421b882b4d0a15e1b48a913c417
---
# Чаты и реалтайм — Execution plan

## Task summary
- Type: feature
- Size: L. Новый контекст во всех слоях (модели, ядро транзакций, репозиторий, сервис, REST, WS); около 15 новых и изменённых файлов в `app/` и 8–10 тестовых модулей.
- Criticality: data-migration и security. Меняются схема БД и unit of work (`core/db.py`, через который идут все коммиты), добавляется новая точка входа с авторизацией (WS).
- Parallelism: две независимые оси.
  - Фаза 1 (схема) и фаза 2 (ядро) не пересекаются ни по файлам, ни по контрактам.
  - Фазы 3b (SQL-репозиторий) и 4 (сервис) идут параллельно после 3a: обе зависят только от Protocol и фейка.
  - Остальное — строго последовательно.

Ветка — `feature/chat-websocket`. Перед каждой фазой нужно подтянуть `main` (`git fetch origin && git merge origin/main`). Если в `main` появилась базовая миграция Alembic или изменился `core/deps.py`, конфликты разрешаются до начала фазы.

Общие правила для всех фаз: `CONTRIBUTING.md` (порядок слоёв и чек-лист), коды ошибок и сигнатуры строго по `ARCHITECTURE.md`. Каждая фаза завершается командами `pytest`, `ruff check .`, `ruff format --check .`, `mypy` — все зелёные.

## Phase 1 — Схема чата (parallel с фазой 2)
- Agent: general-purpose (fallback для роли backend-python; GAP в `planner-context.md` §1)
- Model: haiku (две механические правки одного файла)
- Skills: none
- Inputs: `ARCHITECTURE.md` → Decisions → «Схема БД (дельта к текущим моделям)»; `app/models/chat.py`, `app/models/base.py`; раздел «Основные сущности» в `CLAUDE.md`.
- Work: применить дельту моделей — только две правки.
  - `Message.client_msg_id: UUID | None` и UNIQUE (`sender_id`, `client_msg_id`);
  - индекс `chat_members(user_id)`.

  Типы id и CHECK-ограничения не трогать (см. «Сознательно не делается» в архитектуре). Имена ограничений — через `NAMING_CONVENTION`. Миграция:
  - если в `alembic/` уже есть базовая ревизия — сгенерировать миграцию чата поверх неё и проверить руками;
  - иначе миграцию **не** создавать и записать в описание PR, что её генерирует тот, кто инициализирует Alembic, либо эта ветка после merge базовой.
- Outputs: `app/models/chat.py`, `./CLAUDE.md` (поле `client_msg_id` в `Message`), условно `alembic/versions/`.
- Verification: `python -c "import app.models"` проходит; `mypy` зелёный. При наличии миграции: `alembic upgrade head` и `alembic downgrade -1` на чистой БД проходят, `alembic heads` показывает одну голову. Ревью схемы до фазы 3a (data-migration).

## Phase 2 — Ядро: ошибки, unit of work с outbox, лимитер, события (parallel с фазой 1)
- Agent: general-purpose
- Model: opus. Меняется единственная точка commit всего приложения, а логика хуков `after_commit`/`after_rollback` и привязки outbox к `session.info` легко ломается незаметно.
- Skills: none
- Inputs: `ARCHITECTURE.md` → Contracts → «Unit of work, outbox и Notifier», «Общие DTO»; Decisions 1, 2, 12, 13; `app/core/db.py`, `app/core/deps.py`, `app/core/errors.py`, `app/api/errors.py`, `app/core/config.py`.
- Work:
  - `RateLimitedError` и маппинг в `429`;
  - `session_scope(notifier)` и `get_outbox(session)` в `core/db.py`; `get_session` переписать поверх `session_scope`, не меняя поведения для HTTP;
  - глобальные слушатели `after_commit` (только внешняя транзакция) и `after_rollback`; `publish` не должен ронять commit;
  - `core/rate_limit.py` — token bucket по `user_id` с инъекцией часов для тестов;
  - новые поля `Settings`;
  - `services/notifier.py` — события, `ChatEventSink`, `Notifier`;
  - `schemas/chat.py` — `UserBrief`, `MessageOut`, `ChatOut`, `MessagePage` и запросы `SendMessageRequest`, `ReadRequest`.
- Outputs: `app/core/errors.py`, `app/api/errors.py`, `app/core/db.py`, `app/core/rate_limit.py`, `app/core/config.py`, `app/services/notifier.py`, `app/schemas/chat.py`, `tests/unit/test_rate_limit.py`.
- Verification:
  - существующие `tests/api/test_identity_api.py` и `tests/unit/test_identity_service.py` проходят без изменений — поведение `get_session` сохранено;
  - unit-тесты лимитера: всплеск, пополнение, изоляция пользователей;
  - в `core/` нет импортов из `api/`.

  Независимое ревью фазы до старта фазы 4: хуки, вложенные транзакции, rollback.

## Phase 3a — Контракт репозитория и фейк (после фазы 1)
- Agent: general-purpose
- Model: sonnet
- Skills: none
- Inputs: `ARCHITECTURE.md` → Contracts → «`ChatRepository`»; `app/repositories/interfaces.py`, `tests/fakes/repositories.py`; модели фазы 1.
- Work: в `interfaces.py` добавить frozen-dataclass'ы `UserBriefView`, `MessageView`, `ChatSummaryView` и Protocol `ChatRepository` со всеми методами из архитектуры. В `tests/fakes/repositories.py` добавить `FakeChatRepository` с той же семантикой:
  - идемпотентность `get_or_create_direct`, `add_members`, `add_message` по `client_msg_id`;
  - курсоры и порядок по возрастанию;
  - `advance_read` только вперёд;
  - формула unread;
  - `FakeChatEventSink` и `FakeRateLimiter`.
- Outputs: `app/repositories/interfaces.py`, `tests/fakes/repositories.py`.
- Verification: `mypy` подтверждает, что фейк структурно соответствует Protocol (присваивание `_: ChatRepository = FakeChatRepository()` в тестовом модуле); `pytest` зелёный.

## Phase 3b — SQL-репозиторий и тесты на PostgreSQL (parallel с фазой 4; после 3a)
- Agent: general-purpose
- Model: sonnet
- Skills: none
- Inputs: Protocol из 3a; `ARCHITECTURE.md` → «Реализация в SQL», Verification → «ChatRepository»; `app/repositories/user.py` как образец.
- Work:
  - `SqlChatRepository`: `ON CONFLICT DO NOTHING` + SELECT для direct, участников и `client_msg_id`; один запрос с `LEFT JOIN LATERAL` для списка и карточки чата; курсоры с `limit + 1`; `GREATEST` в `advance_read`. Без `commit`.
  - Тестовая инфраструктура БД: `tests/repositories/conftest.py` берёт `TEST_DATABASE_URL`, создаёт схему через `Base.metadata.create_all`, даёт транзакцию на тест с откатом, а без переменной окружения тесты помечаются `skip`.
  - Тесты гонок в двух отдельных сессиях.
- Outputs: `app/repositories/chat.py`, `tests/repositories/__init__.py`, `tests/repositories/conftest.py`, `tests/repositories/test_chat_repository.py`.
- Verification: при поднятой БД (`docker compose up -d postgres` или локальная PostgreSQL 16) все тесты из Verification → «ChatRepository» проходят: гонки, UNIQUE `client_msg_id`, unread, курсоры, сортировка, title/peer. Без БД они `skipped`, а не `failed`.

## Phase 4 — ChatService и unit-тесты (parallel с фазой 3b; после 2 и 3a)
- Agent: general-purpose
- Model: sonnet
- Skills: none
- Inputs: `ARCHITECTURE.md` → «Внутренний контракт `ChatService`», таблица побочных эффектов, Decisions 5, 8, 9, 10, 12; фейки из 3a; события и схемы из фазы 2.
- Work: `services/chat.py` по контракту.
  - Порядок в `send_message`: членство → поиск дубля по `client_msg_id` → валидация текста → лимитер → вставка → `advance_read` отправителя → событие.
  - `ensure_direct_chat` с ключом `"<min>:<max>"` и `cannot_chat_self`.
  - Групповые методы.
  - Маппинг view → схемы.
  - Без `sqlalchemy` и `commit`.
- Outputs: `app/services/chat.py`, `tests/unit/test_chat_service.py`.
- Verification: каждый пункт Verification → «ChatService» покрыт отдельным тестом, ошибки проверяются по `exc.value.code`; `grep -n sqlalchemy app/services/chat.py` пуст.

## Phase 5 — REST API и DI (после 3b и 4)
- Agent: general-purpose
- Model: sonnet
- Skills: none
- Inputs: `ARCHITECTURE.md` → Contracts → «REST»; `app/api/me.py`, `app/core/deps.py`, `tests/api/conftest.py` как образцы; сервис из фазы 4, SQL-репозиторий из 3b.
- Work:
  - DI: `get_chat_repository`, `get_notifier(conn: HTTPConnection)` из `app.state.connections`, `get_rate_limiter` (синглтон на приложение в `app.state`), `get_chat_service`;
  - в `create_app` создавать `app.state.connections` (пока минимальный no-op `Notifier`, полноценный менеджер — в фазе 6) и лимитер;
  - `api/chats.py` с пятью ручками, `SurveyCompletedUser`, `responses=error_responses(...)`;
  - подключение в `main.py`;
  - подмена `get_chat_repository` в `tests/api/conftest.py`.
- Outputs: `app/core/deps.py`, `app/api/chats.py`, `app/main.py`, `tests/api/conftest.py`, `tests/api/test_chats_api.py`.
- Verification: все пункты Verification → «REST»; `GET /openapi.json` содержит `ChatOut`, `MessageOut`, `MessagePage` и ошибки чата.

## Phase 6 — Реалтайм: ConnectionManager и WebSocket (после 5)
- Agent: general-purpose
- Model: opus. Здесь конкурентность (очереди, task-писатель, вытеснение, idle-таймаут) и новая точка авторизации.
- Skills: none
- Inputs: `ARCHITECTURE.md` → Contracts → «WebSocket», «ConnectionManager», «WS-эндпоинт»; Decisions 2, 4, 11, 13, 14; DI и `create_app` из фазы 5.
- Work:
  - `schemas/ws.py`: `ClientFrame`/`ServerFrame` как discriminated union и добавление их JSON-схем в `app.openapi()`;
  - `api/connections.py`: `Connection`, `ConnectionManager(Notifier)`, сериализация один раз на событие, закрытие `1013` и `4000`;
  - `ChatUnitFactory` в `core/deps.py` на `session_scope(notifier)`, без `SessionDep`;
  - `api/ws.py`: рукопожатие с таймаутом, коды `4401`/`4403`, лимит размера фрейма, idle-таймаут, диспетчеризация `send`/`read`/`ping`, маппинг `DomainError` → `error`, логи без payload;
  - заменить no-op `Notifier` из фазы 5 на менеджер;
  - фабрика на фейках для тестов.
- Outputs: `app/schemas/ws.py`, `app/api/connections.py`, `app/api/ws.py`, `app/core/deps.py`, `app/main.py`, `tests/unit/test_connection_manager.py`, `tests/api/test_ws.py`.
- Verification:
  - все пункты Verification → «ConnectionManager» и «WebSocket» через `starlette.testclient.TestClient`, с таймаутами из `Settings`, уменьшенными до долей секунды;
  - `/openapi.json` содержит `ClientFrame` и `ServerFrame`;
  - caplog: текста сообщения и токена нет в логах.

  Security-ревью фазы до фазы 7.

## Phase 7 — Интеграция, сквозная проверка, демо (после 6)
- Agent: general-purpose
- Model: sonnet
- Skills: run (запуск приложения для ручной проверки)
- Inputs: всё выше; Verification → «Outbox», «Логи», «OpenAPI».
- Work:
  - интеграционный тест outbox на тестовой PostgreSQL: события только после commit, ничего после rollback, исключение в `publish` не ломает commit;
  - сквозной тест: два пользователя → общий чат через `ensure_direct_chat` → сообщение через WS → `GET /chats` показывает `last_message` и `unread_count`, `after_id` догружает;
  - если `seeds/` уже существует — добавить вымышленные демо-пары с готовыми личным и групповым чатами;
  - ручная проверка: две вкладки, отправка, переподключение, unread.
- Outputs: `tests/repositories/test_outbox.py`, `tests/api/test_chat_e2e.py`, условно `seeds/`.
- Verification: все тесты зелёные при поднятой БД. Ручной сценарий из «Критерия готовности контекста» в `ARCHITECTURE.md` воспроизведён, результат записан в описание PR.

## Estimate

Надёжной оценки нет: в проекте нет истории выполнения планов (`planner-context.md` без уроков). Ориентир:
- по токенам — порядка 1.5–2.5 млн на все фазы с ревью;
- по времени — 3–5 часов агентной работы;
- две фазы на Opus (2 и 6) удорожают прогон примерно в 1.6–2× относительно чистого Sonnet;
- параллельность (1‖2 и 3b‖4) сокращает wall-clock примерно на четверть по сравнению с последовательным прогоном, но стоимость не снижает.

## Final review

После фазы 7 — независимое ревью всего диффа ветки относительно `main`:
- `code-review` (уровень high): корректность, гонки, идемпотентность;
- `security-review`: WS-auth, утечка существования чужих чатов, логи, rate limit;
- ручной прогон чек-листа `CONTRIBUTING.md` по слоям.

Отчёты ревью — в `.claude/plans/chat-realtime/review-request-changes/REVIEW-NN.md`. Промежуточные ревью (после фаз 1, 2, 6) — туда же, с номером раунда.

## Documentation

Документацию ведёт исполнитель фазы, в которой меняется факт. Разрешённые пути:
- `./CLAUDE.md` — «Основные сущности» (фаза 1); «Команды», если появился `TEST_DATABASE_URL` или новые команды (фаза 3b);
- `./CONTRIBUTING.md` — строка **chat** в таблице «Что осталось сделать» переносится в готовое (фаза 7);
- `./.env.example` — новые настройки WS и rate limit (фаза 2).

Архитектуру исполнители не правят. Дефект дизайна возвращается в `ARCHITECTURE.md`, после чего этот план перестраивается.

## Risks and gates

| Риск | Митигация | Стоп-условие |
|---|---|---|
| В `main` нет базовой миграции Alembic | Фаза 1 меняет только модели; тесты репозитория строят схему через `create_all` | Не генерировать «базовую» миграцию в этой ветке без согласования с командой |
| Переписанный `get_session` ломает identity | Существующие тесты identity — обязательный гейт фазы 2 | Любой красный тест identity → фаза 2 не завершена |
| Хуки outbox срабатывают на вложенных транзакциях или не срабатывают в WS | Ревью фазы 2 и интеграционный тест в фазе 7 | Событие видно до commit или после rollback → вернуть в фазу 2 |
| Нет PostgreSQL для тестов репозитория | Тесты `skip` без `TEST_DATABASE_URL`; запуск с БД обязателен до merge | Фаза 3b не считается проверенной, пока тесты не прогнаны на реальной БД хотя бы раз |
| Способ WS-auth не подтверждён вебом | Open question в архитектуре | **Фаза 6 не стартует**, пока команда и веб не подтвердили первый фрейм `auth`; другой способ → правка архитектуры и перестройка плана |
| Параллельные 3b и 4 правят общие файлы | 3b трогает только `repositories/chat.py` и `tests/repositories/`, 4 — только `services/chat.py` и `tests/unit/test_chat_service.py` | Потребовалось менять Protocol → остановить обе, вернуться в 3a |
| Конфликт с работой коллег в `core/deps.py` и `main.py` | Merge `origin/main` перед фазами 5 и 6 | Неразрешимый конфликт контракта → обсудить с автором до продолжения |

## Fallback

- Профильного агента backend-python в каталоге нет (GAP): все фазы выполняет `general-purpose` по чек-листу `CONTRIBUTING.md`. Если оркестратор недоступен, фазы выполняются вручную в той же последовательности.
- Нет доступа к Opus → фазы 2 и 6 на Sonnet с обязательным независимым ревью сразу после каждой.
- Нет Docker и PostgreSQL → фазы 1–6 выполняются с unit- и API-тестами на фейках. Фазы 3b и 7 помечаются «не проверено на БД», и merge в `main` откладывается до прогона.
- Фаза 6 заблокирована вопросом про auth → фазы 1–5 можно смержить отдельным PR: REST-чаты работают без реалтайма, `Notifier` остаётся no-op.
