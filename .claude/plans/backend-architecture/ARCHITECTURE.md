---
plan_type: architecture
version: 1
status: current
content_sha256: bf3e8aca5dee5b11aa56b6e856b5027797438b5bed4c0a3d84a2abf2d6694781
---
# Tinterest backend — Architecture

## Context and scope

Бэкенд внутреннего приложения Т-Банка для поиска коллег по интересам. Источник требований — `CLAUDE.md` в корне репозитория (цель, критерии приёмки, сущности, безопасность) и решения синка: порядок «анкета → основная страница (чаты, рекомендации, поиск) → группы», группы по новому интересу создаются пользователями через модерацию, веб и бэк — разные репозитории.

**Критерий приёмки системы:** пользователь регистрируется, заполняет анкету, получает рекомендацию человека или группы и переписывается в чате.

**В scope:** HTTP API и WebSocket для веба, схема PostgreSQL и миграции, вымышленные сиды, Docker-образ бэка и compose для локального запуска/демо.

**Вне scope:** веб-клиент (отдельный репозиторий; потребляет OpenAPI), фильтры, ивенты групп, push/email-уведомления, загрузка фото, подтверждение почты, SSO, горизонтальное масштабирование, админка для редактирования анкеты (в MVP анкета меняется сидами/миграциями).

## Bounded contexts

Один процесс FastAPI (модульный монолит). Каждый контекст — набор файлов в слоях `api/ services/ repositories/ models/ schemas/` с одинаковым именем модуля. Контексты общаются только через сервисы, не через чужие репозитории (исключение — чтение справочников, явно указанное ниже).

| Контекст | Отвечает за | Таблицы | Граница |
|---|---|---|---|
| **identity** | регистрация, вход, JWT, текущий пользователь, профиль, справочник офисов, роли | `users`, `offices` | единственный, кто знает про пароли и токены; другие получают `CurrentUser` через DI |
| **survey** | активные вопросы, варианты, приём ответов, вывод интересов пользователя, флаг `survey_completed` | `questions`, `answer_options`, `user_answers`, `interests`, `user_interests` | единственный писатель `user_interests`; дерево интересов отдаёт read-only |
| **matching** | рекомендации людей, пара по одному like, личный skip | `matches` | читает интересы и офисы через `SurveyService`/`IdentityService`; chat пока заглушка, будущий вызов `ChatService.ensure_direct_chat` |
| **groups** | группы, членство, заявки на новые группы, модерация, рекомендации групп | `groups`, `group_members` | создаёт групповой чат через `ChatService.create_group_chat` при одобрении/вступлении |
| **chat** | чаты, участники, сообщения, непрочитанные, реалтайм-доставка | `chats`, `chat_members`, `messages` | не знает, почему чат создан; проверяет только членство |
| **search** | поиск людей и одобренных групп по строке | — (читает чужие таблицы) | read-only; единственное разрешённое исключение — собственный `SearchRepository` с SELECT по `users`, `groups`, `interests` |

Общий код — `app/core/`: конфиг (`pydantic-settings`), async engine и `get_session`, безопасность (хэш паролей, JWT), доменные исключения и их маппинг в HTTP, DI-фабрики сервисов.

## Contracts

### Общие правила API

- Префикс `/api/v1`. JSON, `snake_case`. Время — ISO-8601 UTC.
- Авторизация: `Authorization: Bearer <access_jwt>` на всех эндпоинтах, кроме `POST /auth/register`, `POST /auth/login`, `GET /health`.
- Ошибки — единый формат `{"error": {"code": "<machine_code>", "message": "<human text>"}}`. Сервисы бросают доменные исключения из `core/errors.py`; обработчики в `api` маппят их: `NotFound→404`, `Forbidden→403`, `Conflict→409`, `DomainValidation→422`, `Unauthorized→401`. Ошибки валидации Pydantic приводятся к тому же формату с кодом `validation_error`.
- Пагинация списков — курсор `?before_id=&limit=` (limit ≤ 100, по умолчанию 30) для сообщений; `?limit=&offset=` для остального.
- Пока анкета не заполнена (`survey_completed = false`), эндпоинты matching, groups, chat, search отвечают `403 survey_required`. Это зависимость `require_survey_completed` в `core/deps.py`.

### identity

| Метод | Вход | Выход | Ошибки |
|---|---|---|---|
| `POST /auth/register` | email, password (≥ 8), name, office_id | `201` + `TokenPair` | `409 email_taken`, `422 email_domain_not_allowed`, `422 office_not_found` |
| `POST /auth/login` | email, password | `TokenPair` | `401 invalid_credentials` (одинаково для «нет пользователя» и «неверный пароль») |
| `POST /auth/refresh` | refresh_token | `TokenPair` | `401 invalid_token` |
| `GET /me` | — | `Me` (id, email, name, about, office, role, survey_completed) | — |
| `PATCH /me` | name?, about? (≤ 500), office_id? | `Me` | `422 office_not_found` |
| `GET /users/{id}` | — | `PublicProfile` (name, about, office, интересы, свободные ответы) | `404 user_not_found` |
| `GET /offices` | — | список офисов | — |

`TokenPair = {access_token, refresh_token, token_type}`. Access живёт 30 мин, refresh — 14 дней; оба JWT HS256 с `sub=user_id`, `type=access|refresh`. Refresh stateless (без таблицы отзыва в MVP).

### survey

| Метод | Вход | Выход | Ошибки |
|---|---|---|---|
| `GET /survey` | — | активные вопросы по `order` с вариантами; у варианта `interest_id?` | — |
| `GET /survey/answers` | — | текущие ответы пользователя | — |
| `PUT /survey/answers` | `[{question_id, option_ids?: [int], text_value?: str}]` | `Me` с `survey_completed = true` | `422 unknown_question`, `422 option_not_in_question`, `422 single_choice_violation`, `422 required_question_missing`, `422 text_too_long` |
| `GET /interests` | — | дерево направление → поднаправления | — |

`PUT` — полная замена ответов в одной транзакции: удалить старые `user_answers`, вставить новые, пересчитать `user_interests` из `answer_options.interest_id`, выставить `survey_completed`. Повторный `PUT` из профиля — тот же путь. Правила валидации: `single` — ровно один option; `multi` — ≥ 1 option; `text` — только `text_value` ≤ 300 символов; обязательные — все активные вопросы, кроме `text`.

Уточнения реализованного survey: PUT заменяет ответы только активных вопросов, сохраняя
историю отключённых. `user_interests` пересчитывается по выбранным одобренным
поднаправлениям; дубликаты интересов объединяются. Блокировка строки пользователя
через identity сериализует конкурентные PUT, все записи используют общую SessionDep.
Text после strip должен быть непустым; поле ограничено 300 символами до strip.
Символ U+0000 запрещён во входном тексте: 422 `validation_error` до записи в БД.
Дополнительные 422: `duplicate_question`, `duplicate_option`, `multi_choice_violation`,
`invalid_answer_type`, `empty_text`; неверная структура — `validation_error`.
Нет активных обязательных вопросов или выбран вариант с некорректным интересом —
409 `survey_not_configured`. «Другое» — отдельный необязательный text-вопрос.
Демонстрационные сиды не утверждают окончательный состав анкеты.
При изменении активности вопросов/связей вариантов требуется явный пересчёт интересов
затронутых пользователей; автоматического сброса `survey_completed` нет.

### matching

| Метод | Вход | Выход | Ошибки |
|---|---|---|---|
| `GET /recommendations/people` | `limit` (1..20, default 20; без offset) | `[{user: PublicProfile, score, common_interests: [Interest], same_office: bool}]` | `401`, `403 survey_required`, `404 user_not_found` (удаление после авторизации), `422 validation_error` |
| `POST /matches/{user_id}/like` | — | `{matched: true, chat_id: int \| null}` (сейчас `null`) | `401`, `403 survey_required`, `404 user_not_found`, `422 cannot_match_self` / `validation_error`, `409 target_survey_required` |
| `POST /matches/{user_id}/skip` | — | `204` без тела | `401`, `403 survey_required`, `404 user_not_found`, `422 cannot_match_self` / `validation_error`, `409 target_survey_required` |

Побочный эффект первого `like`: upsert строки `matches` (порядок `user_a_id < user_b_id`) и `SELECT FOR UPDATE`, выставить только свою сторону `accepted`, зафиксировать текущий score. Одного `accepted` достаточно для пары; ответ всегда `matched = true`. Повторы с любой стороны не меняют статусы и score. `skip` выставляет свою сторону `declined` только пока пара не создана: человек скрывается лишь у нажавшего. Like после пропуска разрешён, чужой пропуск не запрещает пару. Запись только с пропусками имеет score 0. Любое действие требует заполненной анкеты адресата; общие интересы или офис для действия не обязательны. ID положительный.

Рекомендации, like и skip реализованы. Создание чата пока заглушка `_ensure_direct_chat` с закомментированным вызовом `ChatService.ensure_direct_chat(a, b)`: `chat_id = null`, чатов и событий нет. При интеграции внедрить ChatService с той же сессией; вызывать его и при повторном like, чтобы создать недостающий чат. Пара и чат сохраняются одной транзакцией, события — после commit. Отдельного `GET /matches` нет; после интеграции используется `GET /chats`.
`PublicProfile = {id, name, about, office, interests, free_answers}`; email и роль не выдаются.
`free_answers = [{question_id, question_text, text_value}]` содержит только активные text-вопросы.
Интересы — одобренные поднаправления с одобренным родителем, по `(name, id)`;
свободные ответы — по `(question.order, question.id)`.
Префильтр применяет исключения до лимита 500 и берёт кандидатов по ID по возрастанию.
Затем сортировка по `(-score, user_id)` и top-N; это не глобальный top-N всей базы.
Данные загружаются пакетно через сервисы survey/identity. Офис и одобренные интересы кандидатов
фиксируются один раз для скоринга, ранжирования и публичного профиля; повторного чтения этих
полей после ранжирования нет. Свободные ответы загружаются только для top-N.
Запрос рекомендаций не создаёт и не меняет строки matches.

### groups

| Метод | Вход | Выход | Ошибки |
|---|---|---|---|
| `GET /recommendations/groups` | `limit` | одобренные группы по интересам пользователя, где он не участник | — |
| `GET /groups/{id}` | — | группа + число участников + `is_member` | `404 group_not_found` (в т.ч. `pending` для чужих) |
| `POST /groups/{id}/join` | — | `{chat_id}` | `404`, `409 already_member` |
| `POST /groups/{id}/leave` | — | `204` | `404`, `409 not_member`, `409 owner_cannot_leave` |
| `POST /groups` | name, description, interest_id? **или** new_interest {name, parent_id} | `201` группа со `status = pending` | `409 group_exists_for_interest`, `409 interest_exists` (новый интерес совпал с существующим по имени без учёта регистра), `422 parent_not_direction` |
| `GET /moderation/groups` | `status=pending` | заявки | `403 forbidden` для role = user |
| `POST /moderation/groups/{id}/approve` | — | группа `approved` | `403`, `404`, `409 not_pending` |
| `POST /moderation/groups/{id}/reject` | reason? | группа `rejected` | `403`, `404`, `409 not_pending` |

`approve` в одной транзакции: `status = approved`, новый интерес (если был) становится видимым (`interests.is_approved = true`), автор — `owner` в `group_members`, `ChatService.create_group_chat(group_id, [author])`. `join` добавляет в `group_members` и `chat_members`.

### chat — REST

| Метод | Вход | Выход | Ошибки |
|---|---|---|---|
| `GET /chats` | — | `[{id, type, title, group_id?, peer?: PublicProfile, last_message?, unread_count}]`, по времени последнего сообщения | — |
| `GET /chats/{id}/messages` | `before_id?`, `limit` | сообщения по убыванию id | `404 chat_not_found` (и для не-участника — не раскрываем существование) |
| `POST /chats/{id}/read` | `message_id` | `204` | `404` |

### chat — WebSocket

`GET /api/v1/ws` — одно соединение на вкладку браузера, мультиплексирует все чаты пользователя.

1. Клиент подключается без токена в URL (query попадает в логи прокси). Первое сообщение в течение 5 с: `{"type": "auth", "token": "<access_jwt>"}`. Иначе сервер закрывает с кодом `4401`.
2. Клиент → сервер:
   - `{"type": "send", "chat_id", "text", "client_msg_id"}` — text 1..4000 символов после `strip`;
   - `{"type": "read", "chat_id", "message_id"}`;
   - `{"type": "ping"}`.
3. Сервер → клиент:
   - `{"type": "auth_ok"}`;
   - `{"type": "message", "message": {id, chat_id, sender_id, text, created_at}}` — всем подключённым участникам, включая отправителя;
   - `{"type": "ack", "client_msg_id", "message_id"}` — только отправителю;
   - `{"type": "error", "code", "client_msg_id?"}` — коды `not_member`, `validation_error`, `rate_limited`;
   - `{"type": "chat_created", "chat": {...}}` — при создании пары одним like или вступлении в группу (после интеграции chat);
   - `{"type": "pong"}`.
4. Истёкший access-токен на открытом соединении не разрывает его; повторная авторизация не требуется до переподключения.

Каждое входящее `send`/`read` обрабатывается в **своей** сессии БД (`async with session_factory()`), коммит до рассылки. Рассылка только после успешного коммита.

### Внутренние контракты сервисов

- `ChatService.ensure_direct_chat(user_a, user_b) -> Chat` — идемпотентно: существующий личный чат пары или новый. Уникальность обеспечивается колонкой `chats.direct_key = "<min>:<max>"` с UNIQUE.
- `ChatService.create_group_chat(group_id, member_ids) -> Chat`; `ChatService.add_member(chat_id, user_id)`; `remove_member`.
- `ChatService` после коммита публикует события в `Notifier` (интерфейс в `services/`, реализация `ConnectionManager` в `api/ws.py`); в HTTP-запросах события копятся в per-request буфере и отправляются после `commit` в `get_session`.
- `SurveyService.get_matching_profile(user_ids) -> dict[int, MatchingProfile]` — интересы и локация текущего пользователя для префильтра и скоринга. Интересы кандидатов — через `SurveyService.get_user_interests(user_ids)`, свободные ответы выбранных пользователей — через `get_free_answers(user_ids)`; данные анкеты matching читает только через SurveyService.

## Data flow

1. **Онбординг.** `POST /auth/register` → `IdentityService` проверяет домен email из `ALLOWED_EMAIL_DOMAINS`, хэширует argon2, создаёт `users` → `TokenPair`. Веб видит `survey_completed = false` в `GET /me` → `GET /survey` → `PUT /survey/answers` → `SurveyService` валидирует, заменяет ответы, пересчитывает `user_interests`, ставит флаг → коммит.
2. **Рекомендации людей.** `GET /recommendations/people` → `MatchingService`: `MatchRepository` отдаёт id исключений (сам пользователь, пары с любым `accepted`, собственные `declined`) → профиль текущего пользователя через `SurveyService.get_matching_profile` → `SurveyService.list_matching_candidate_ids` вызывает SQL-префильтр SurveyRepository: заполненная анкета и ≥ 1 общий интерес **или** тот же офис, исключения до лимита 500, порядок по ID → пакетные данные кандидатов через `IdentityService.get_public_profiles` и `SurveyService.get_user_interests` → `score()` на этих данных → сортировка по `(-score, user_id)` → top-N → `SurveyService.get_free_answers` только для выбранных пользователей. Публичный профиль и объяснение используют те же офисы и интересы, что скоринг.
3. **Матч → чат.** A лайкает B → upsert и блокировка `matches` → A=accepted, фиксируется score → заглушка chat → коммит → `{matched: true, chat_id: null}`. После подключения chat заглушка заменяется на `ensure_direct_chat` в той же транзакции; `chat_created` обоим отправляется после commit. Повторный like тоже обеспечивает наличие чата.
4. **Сообщение.** WS `send` → проверка членства (`ChatRepository.is_member`) → INSERT `messages` → коммит → `ConnectionManager` шлёт `message` всем онлайн-участникам и `ack` отправителю. Офлайн-участники увидят сообщение через `GET /chats` (unread) и `GET /chats/{id}/messages`.
5. **Новая группа.** `POST /groups` с `new_interest` → создаётся `interests (is_approved = false)` + `groups (pending)` → модератор `GET /moderation/groups` → `approve` → интерес виден, группа видна, групповой чат с автором. Чужие пользователи до одобрения получают 404.
6. **Поиск.** `GET /search?q=&type=people|groups` → `SearchService` нормализует строку (strip, ≥ 2 символа) → `SearchRepository`: `ILIKE` по `users.name`, `groups.name`, `interests.name` (для групп — также по интересу группы), только `survey_completed` пользователи и `approved` группы, limit 20.

## Decisions

1. **Модульный монолит, один процесс, один воркер uvicorn.** WS-рассылка через in-memory `ConnectionManager: dict[user_id, set[WebSocket]]`. Обоснование: масштаб — одна компания, демо на одном хосте; Redis в стеке отсутствует и не нужен. Путь роста — Postgres `LISTEN/NOTIFY`, не внешний брокер.
2. **Слои по CLAUDE.md, сервисы зависят от протоколов.** Интерфейсы репозиториев — `typing.Protocol` в `app/repositories/interfaces.py`; сервисы типизированы протоколами, конкретные классы подставляются в `core/deps.py`. Фейки для unit-тестов — в `tests/fakes/`.
3. **Unit of work = `get_session`.** Одна `AsyncSession` на HTTP-запрос, `commit` после успешного выхода из эндпоинта, `rollback` при исключении. Сервисы вызывают `session.flush()` через репозиторий, если нужен id; `commit` не вызывают. WS — отдельная сессия на сообщение (см. Contracts).
4. **Рекомендации считаются на лету, `matches` хранит действия.** Строка появляется при первом like/skip. Score сохраняется при создании пары; в записи только с пропусками он равен 0. Обоснование: нет фоновых задач и инвалидации кэша; на сотнях–тысячах пользователей с SQL-префильтром это миллисекунды.
5. **Скоринг (`services/matching.py`, чистые функции):** `score = jaccard(interests_a, interests_b) + 0.15 * same_office + 0.05 * (same_city and not same_office)`, округление до 3 знаков. `common_interests` и `same_office` возвращаются в ответе — рекомендация объяснима. Коэффициенты — константы модуля, не конфиг.
6. **Интересы — дерево из двух уровней** (`interests.parent_id`), матчинг по листьям (поднаправлениям). Пользовательские интересы из заявок на группу получают `is_approved = false` до модерации.
7. **Вопросы не удаляются.** `questions.is_active`; ответы на неактивные вопросы остаются в БД, но не участвуют в валидации и выдаче.
8. **Одного like достаточно для пары и будущего личного чата.** Вторая сторона не обязана отвечать. Ответный или повторный like не меняет статусы состоявшейся пары. Chat пока подключён заглушкой.
9. **Skip — личное скрытие, без срока действия.** Хранится как `declined` своей стороны и не запрещает другой стороне создать пару. Like после skip разрешён; skip созданной пары ничего не меняет. Блокировки и отмена пары — отдельные будущие функции.
10. **Пароли — argon2** (`pwdlib[argon2]`), JWT — `pyjwt`. Секрет и TTL — в `.env`.
11. **Поиск — `ILIKE`**, индекс `pg_trgm` добавляется миграцией, когда понадобится. Внешних поисковых движков нет.
12. **Сиды идемпотентны**: `python -m seeds` делает upsert по естественному ключу (имя интереса + родитель, текст вопроса, город + офис). Только вымышленные данные; демо-пользователи — `user1@example.test` и т.п.
13. **Миграции — отдельный one-shot сервис compose** `migrate` (`alembic upgrade head`) перед `backend`; автогенерация проверяется руками.
14. **Логирование**: структурные логи с `user_id`, методом, путём, статусом, временем. Тела запросов, email, имена и тексты сообщений не логируются; middleware и обработчики ошибок не выводят payload.
15. **Rate limit**: `/auth/login` — 10 попыток/мин на IP, WS `send` — 10 сообщений/с на соединение. Реализация in-memory (следствие решения 1).

### Схема БД (дельта к сущностям CLAUDE.md)

- `users`: + `about varchar(500)`, `role` enum(`user`,`moderator`,`admin`) default `user`, `office_id` FK NOT NULL, `email` UNIQUE (хранится в нижнем регистре).
- `offices`: `id, city, name`, UNIQUE (city, name).
- `interests`: `id, name, parent_id?` FK self, `is_approved bool default true`, UNIQUE (lower(name), parent_id).
- `answer_options`: + `interest_id?` FK; `order`.
- `user_answers`: `id`, CHECK (`option_id` IS NOT NULL) <> (`text_value` IS NOT NULL), UNIQUE (user_id, question_id, option_id).
- `matches`: `a_status`, `b_status` enum(`pending`,`accepted`,`declined`), CHECK (`user_a_id < user_b_id`), UNIQUE (user_a_id, user_b_id), `updated_at`.
- `groups`: + `created_by` FK, `status` enum(`pending`,`approved`,`rejected`), `reject_reason?`; частичный UNIQUE (interest_id) WHERE status IN (`pending`,`approved`) — одна активная группа на интерес.
- `chats`: + `direct_key varchar UNIQUE NULL`, CHECK (type = `group`) = (group_id IS NOT NULL).
- `chat_members`: + `last_read_message_id?`, `joined_at`.
- `messages`: индекс (chat_id, id DESC).
- Все `created_at timestamptz default now()`.

### Раскладка файлов

```
app/
  main.py                      # create_app(): роутеры, обработчики ошибок, lifespan
  core/{config,db,security,errors,deps,logging}.py
  api/{auth,me,survey,matching,groups,moderation,chats,search,ws,health}.py
  services/{identity,survey,matching,groups,chat,search,notifier}.py
  repositories/{interfaces,user,office,survey,interest,match,group,chat,search}.py
  models/{base,user,survey,match,group,chat}.py
  schemas/{auth,user,survey,matching,group,chat,search,errors}.py
seeds/__main__.py, seeds/data.py
alembic/
tests/{unit,repositories,api}/, tests/fakes/
Dockerfile, docker-compose.yml, .env.example, pyproject.toml
```

Зависимости: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2`, `asyncpg`, `alembic`, `pydantic>=2`, `pydantic-settings`, `pyjwt`, `pwdlib[argon2]`, `email-validator`; dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`.

## Verification boundaries

- **Скоринг** — unit-тесты чистых функций: пустые множества (Jaccard 0, без деления на ноль; бонус локации сохраняется), полное совпадение = 1.0 + бонус, бонус офиса не суммируется с бонусом города, детерминированная сортировка при равном score.
- **Сервисы** (фейковые репозитории):
  - survey: каждое правило валидации даёт свой код ошибки; повторный `PUT` полностью заменяет ответы и интересы; `survey_completed` выставляется только при полном наборе обязательных;
  - matching: один like создаёт пару, автоматического accepted у второй стороны нет; оба исчезают из рекомендаций; skip скрывает только у себя; like после skip разрешён; повторы сохраняют score и статусы; chat_id пока null;
  - groups: вторая заявка на тот же интерес → `409`; `approve` не-модератором → `403`; `pending` группа недоступна чужим;
  - chat: не-участник не читает и не пишет.
- **Репозитории** (тестовая PostgreSQL, транзакция на тест с откатом): ограничения схемы реально срабатывают — CHECK порядка в `matches`, UNIQUE `direct_key`, частичный UNIQUE групп, CHECK `user_answers`.
- **API** (`httpx.AsyncClient` + тестовая БД): каждый защищённый эндпоинт без токена → `401`; эндпоинты после анкеты без анкеты → `403 survey_required`; формат ошибок единый.
- **WebSocket** (`TestClient.websocket_connect`): без `auth` за 5 с → закрытие `4401`; сообщение доходит второму участнику и `ack` отправителю; `send` в чужой чат → `error not_member`, в БД ничего не записано.
- **Сквозной сценарий matching** — регистрация двух пользователей → анкеты с общим интересом → один like → пара сохранена, оба исключены из рекомендаций. После интеграции chat: обмен сообщением через WS → `GET /chats` показывает чат и unread.
- **Конкурентность matching** — параллельные like/like, like/skip, skip/skip дают одну каноническую строку; like сохраняет пару, skip не разрывает её. Ошибка интеграции или commit откатывает создание и изменение пары.
- **Безопасность**: тест, что логи запроса регистрации и отправки сообщения не содержат email и текст (caplog); `.env` в `.gitignore`.
- **OpenAPI**: `GET /openapi.json` генерируется без ошибок; схемы ответов объявлены через `response_model`.

## Risks

| Риск | Митигация |
|---|---|
| In-memory WS не переживает несколько воркеров/реплик | Явно один воркер в Dockerfile/compose; рассылка спрятана за `Notifier`, замена на `LISTEN/NOTIFY` не трогает сервисы |
| События WS уходят до коммита или теряются при откате | Буфер событий в рамках сессии, отправка только после успешного `commit` |
| Рекомендации на лету замедлятся при росте | SQL-префильтр + лимит 500 кандидатов; при необходимости — таблица кэша скоров, контракт API не меняется |
| Гонка двух одновременных like → два чата | `ensure_direct_chat` через `INSERT … ON CONFLICT (direct_key) DO NOTHING` + повторный SELECT |
| Гонка двух заявок на один интерес | частичный UNIQUE на `groups.interest_id`; `IntegrityError` → `409` |
| Утечка персональных данных в логи | логирующий middleware без тел; тест caplog |
| Решения по открытым вопросам изменятся (чат без матча, свайпы) | Логика доступа к личному чату сосредоточена в `MatchingService.like` и `ChatService.ensure_direct_chat`; смена правила — одно место |
| Stateless refresh нельзя отозвать | Короткий access (30 мин); таблица отзыва — после MVP |

## Open questions

| Вопрос | Владелец | Текущее допущение |
|---|---|---|
| Выдача людей: карточки или список | команда (веб) | API отдаёт список top-N; карточки строятся на клиенте из того же ответа |
| Что именно ищет поиск | команда | люди по имени, группы по названию и интересу |
| Состав анкеты кроме интересов, кто пишет список интересов | команда | сиды с вымышленным черновиком, правится PR-ом |
| Фото профиля | команда | нет в MVP |
| Ограничение регистрации доменом | заказчик | список доменов в `.env`; для демо — `example.test` |

## Hand-off

Для планирования выполнения: эта архитектура и `CLAUDE.md`. Предлагаемый порядок фаз (соответствует приоритетам синка):

1. **Каркас:** `pyproject.toml`, `core/` (config, db, errors, logging), `main.py`, `/health`, Alembic, Dockerfile, compose (postgres, migrate, backend), ruff/mypy/pytest, тестовая БД.
2. **identity:** модели `users`/`offices`, регистрация/вход/refresh/`/me`, `get_current_user`, `require_survey_completed`, сиды офисов.
3. **survey:** модели анкеты и интересов, `SurveyService` с валидацией, `PUT /survey/answers`, сиды вопросов и интересов. *Ближайшая цель к синку.*
4. **chat:** модели чатов, REST-история, `ConnectionManager`, WS-протокол, буфер событий после коммита. *Начать к синку.*
5. **matching:** скоринг + тесты, рекомендации людей, like/skip, заглушка `ensure_direct_chat`; подключение chat отдельным этапом.
6. **groups + moderation:** заявки, модерация, вступление, рекомендации групп.
7. **search** и сквозной тест приёмки.

Фазы 3 и 4 независимы после фазы 2 и могут идти параллельно разными людьми.
