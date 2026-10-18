---
plan_type: architecture
version: 2
status: current
content_sha256: 955504574c45402b82e404eafca7fe5cca097421b882b4d0a15e1b48a913c417
---
# Чаты и реалтайм — Architecture

## Context and scope

Детальная архитектура контекста **chat** бэкенда Tinterest. Основа — `CLAUDE.md` и общая архитектура `.claude/plans/backend-architecture/ARCHITECTURE.md` (разделы «chat — REST», «chat — WebSocket», решения 1, 3, 15). Этот документ уточняет и **заменяет** там раздел про чат; расхождения перечислены в Decisions → «Изменения относительно общей архитектуры».

Что уже есть в коде (`main`, коммит `f4fb501`): модели `Chat`, `ChatMember`, `Message` (`app/models/chat.py`), identity-контекст как образец слоёв, `get_session` с единственным `commit`, DI в `app/core/deps.py`, доменные ошибки и их маппинг, фейковые репозитории для тестов. Alembic ещё не инициализирован, `ChatRepository`/`ChatService`/WS отсутствуют.

**В scope:**
- личные (direct) и групповые (group) чаты: список, карточка, история сообщений, отправка, отметка прочитанного, счётчик непрочитанных;
- реалтайм-доставка через одно WebSocket-соединение на вкладку браузера;
- внутренний API для matching (создать личный чат при взаимном лайке) и groups (групповой чат, вступление и выход участников);
- доставка событий только после коммита, идемпотентная отправка, rate limit;
- дельта схемы БД и миграция, тесты всех уровней.

**Вне scope:** редактирование и удаление сообщений, вложения и фото, «печатает…», read receipts для собеседника, push/email, поиск по сообщениям, несколько воркеров/реплик, создание личного чата без матча (см. Open questions), UI.

**Критерий готовности контекста:** два пользователя с общим личным чатом обмениваются сообщениями через WS в реальном времени. После переподключения каждый догружает пропущенное через REST, а `GET /chats` показывает корректный `unread_count`.

## Bounded contexts

| Часть | Слой и файл | Отвечает за | Не знает про |
|---|---|---|---|
| **ChatRepository** | `repositories/chat.py` (+ Protocol в `interfaces.py`) | все SQL-запросы к `chats`, `chat_members`, `messages`; read-only join с `users` (имя) и `groups` (название) для отображения | бизнес-правила, события, `commit` |
| **ChatService** | `services/chat.py` | членство, валидация текста, идемпотентность, создание чатов, публикация событий в outbox | SQLAlchemy, WebSocket, HTTP |
| **События и Notifier** | `services/notifier.py` | типы доменных событий чата и протоколы `ChatEventSink` (куда сервис кладёт события) и `Notifier` (кто доставляет) | транспорт |
| **Outbox и unit of work** | `core/db.py` | `session_scope()` — единый commit/rollback для HTTP и WS; outbox сессии отдаёт события в `Notifier` только после успешного commit и выбрасывает их при rollback | содержимое событий |
| **RateLimiter** | `core/rate_limit.py` | token bucket в памяти по `user_id` | чаты |
| **ConnectionManager** | `api/connections.py` | реестр соединений `user_id → set[Connection]`, очередь отправки и писатель на каждое соединение, превращение событий во фреймы; реализует `Notifier` | БД |
| **WS-эндпоинт** | `api/ws.py` | рукопожатие и auth, чтение фреймов, диспетчеризация в `ChatService` (своя сессия на каждый фрейм), маппинг доменных ошибок во фреймы `error` | SQL |
| **REST-эндпоинты** | `api/chats.py` | HTTP-обёртки над `ChatService` | SQL, WS |
| **Схемы** | `schemas/chat.py`, `schemas/ws.py` | DTO REST и фреймы WS (discriminated union), видимые в OpenAPI | — |

Границы с соседями:
- **matching → chat:** только `ChatService.ensure_direct_chat(a, b)`.
- **groups → chat:** только `create_group_chat`, `add_group_member`, `remove_group_member`.
- **chat → identity:** только `decode_token` и `UserRepository.get_by_id` при авторизации WS (через ту же DI-сборку, что `get_current_user`).
- Chat не знает, *почему* чат создан, и проверяет только членство в `chat_members`.

## Contracts

### Общие правила

- Префикс `/api/v1`. Все REST-ручки чата требуют `SurveyCompletedUser`: без токена `401`, без анкеты `403 survey_required`.
- Чужой чат и несуществующий чат неразличимы: `404 chat_not_found` в REST и `error chat_not_found` в WS. Существование чужого чата не раскрывается.
- Текст сообщения: `strip()`, затем длина 1..4000 символов. Пустой после strip → `422 message_empty`, длиннее → `422 message_too_long`. Pydantic проверяет только верхнюю границу сырой строки (≤ 4000 + запас 100 на пробелы); итоговое правило — в сервисе.
- Новый доменный класс `RateLimitedError` → HTTP `429 rate_limited`; добавляется в `core/errors.py` и в `_STATUS_BY_ERROR`.

### Общие DTO (`schemas/chat.py`)

```text
UserBrief   = {id: int, name: str}
MessageOut  = {id: int, chat_id: int, sender: UserBrief, text: str,
               created_at: datetime, client_msg_id: UUID | null}
ChatOut     = {id: int, type: "direct" | "group", title: str,
               group_id: int | null, peer: UserBrief | null,
               last_message: MessageOut | null, unread_count: int,
               last_read_message_id: int | null, created_at: datetime}
MessagePage = {items: [MessageOut], has_more: bool}
```

- `title`: для direct это имя собеседника, для group — `groups.name`. `peer` заполнен только для direct.
- `unread_count` = число сообщений чата с `id > coalesce(last_read_message_id, 0)` и `sender_id != me`.

### REST

| Метод | Вход | Выход | Ошибки |
|---|---|---|---|
| `GET /chats` | — | `[ChatOut]`, сортировка по `coalesce(last_message.created_at, chat.created_at)` desc, затем `id` desc; не более 200 | — |
| `GET /chats/{chat_id}` | — | `ChatOut` | `404 chat_not_found` |
| `GET /chats/{chat_id}/messages` | `before_id?`, `after_id?`, `limit` (1..100, по умолчанию 30) | `MessagePage`, `items` **всегда по возрастанию id** | `404 chat_not_found`, `422 validation_error` (если переданы оба курсора) |
| `POST /chats/{chat_id}/messages` | `{text, client_msg_id?: UUID}` | `201 MessageOut` (при повторе с тем же `client_msg_id` — тот же объект) | `404`, `422 message_empty`, `422 message_too_long`, `429 rate_limited` |
| `POST /chats/{chat_id}/read` | `{message_id}` | `204` | `404`, `422 message_not_in_chat` |

Семантика курсоров:
- без курсора — последние `limit` сообщений;
- `before_id` — `limit` ближайших сообщений с `id < before_id` (листание вверх);
- `after_id` — `limit` ближайших с `id > after_id` (догрузка после переподключения).

`has_more` показывает, есть ли ещё сообщения в направлении курсора. Репозиторий берёт `limit + 1` строк.

`read` двигает указатель только вперёд: `last_read = GREATEST(coalesce(last_read, 0), message_id)`. Отметка старого сообщения — не ошибка, а no-op.

### WebSocket `GET /api/v1/ws`

Одно соединение на вкладку, мультиплексирует все чаты пользователя. Фреймы — JSON-текст, не больше 16 KB (больше → закрытие `1009`).

**Рукопожатие.**
1. Сервер делает `accept` без проверки: токена в URL нет, чтобы он не попадал в логи прокси.
2. Первый фрейм в течение `ws_auth_timeout_seconds` (по умолчанию 5) должен быть `{"type": "auth", "token": "<access_jwt>"}`.
3. Сервер проверяет токен (`decode_token(..., "access")`), наличие пользователя и `survey_completed`.
4. Успех → `{"type": "auth_ok", "user_id"}`, соединение регистрируется в `ConnectionManager`.

Коды закрытия:

| Код | Когда |
|---|---|
| `4401` | нет `auth` вовремя, первый фрейм не `auth`, токен недействителен, пользователь не найден |
| `4403` | анкета не заполнена |
| `4000` | вытеснено: у пользователя открыто больше `ws_max_connections_per_user` (по умолчанию 5), закрывается самое старое соединение |
| `1013` | очередь отправки переполнена (медленный клиент); клиент переподключается и догружает историю через `after_id` |
| `1000` | нет входящих фреймов дольше `ws_idle_timeout_seconds` (по умолчанию 60); клиент шлёт `ping` раз в 25 с |

**Клиент → сервер.**

| Фрейм | Поля | Ответ |
|---|---|---|
| `auth` | `token` | `auth_ok` или закрытие |
| `send` | `chat_id`, `text`, `client_msg_id: UUID` (обязателен) | `ack` отправителю + `message` всем участникам |
| `read` | `chat_id`, `message_id` | `read` во все соединения этого же пользователя |
| `ping` | — | `pong` |

**Сервер → клиент.**

| Фрейм | Поля | Кому |
|---|---|---|
| `auth_ok` | `user_id` | этому соединению |
| `message` | `message: MessageOut` | всем онлайн-соединениям всех участников чата, включая другие вкладки отправителя и само отправившее соединение |
| `ack` | `client_msg_id`, `message: MessageOut` | отправившему соединению |
| `read` | `chat_id`, `message_id` | всем соединениям прочитавшего (синхронизация счётчика между вкладками) |
| `chat_created` | `chat_id` | новым участникам; клиент запрашивает `GET /chats/{chat_id}` |
| `chat_removed` | `chat_id` | удалённому участнику |
| `error` | `code`, `message`, `client_msg_id?`, `chat_id?` | этому соединению; соединение не закрывается |
| `pong` | — | этому соединению |

Коды `error`: `bad_frame` (невалидный JSON, неизвестный `type`, ошибка схемы), `chat_not_found`, `message_empty`, `message_too_long`, `message_not_in_chat`, `rate_limited`, `internal_error`.

Порядок `ack` и `message` для отправителя не гарантирован. Клиент дедуплицирует по `message.id` и сопоставляет оптимистичное сообщение по `client_msg_id`.

Истёкший access-токен не разрывает открытое соединение. При переподключении нужен свежий токен.

**OpenAPI.** WS-фреймы не видны FastAPI автоматически. `schemas/ws.py` объявляет `ClientFrame` и `ServerFrame` как `Annotated[Union[...], Field(discriminator="type")]`. `create_app` переопределяет `app.openapi()` и добавляет их JSON-схемы (`TypeAdapter(...).json_schema(ref_template=...)`) в `components.schemas`, чтобы фронт сгенерировал типы вместе с REST.

### Внутренний контракт `ChatService`

```python
class ChatService:
    def __init__(self, chats: ChatRepository, events: ChatEventSink, limiter: RateLimiter) -> None

    # чтение
    async def list_chats(self, user_id: int) -> list[ChatOut]
    async def get_chat(self, user_id: int, chat_id: int) -> ChatOut                       # NotFound
    async def get_messages(self, user_id: int, chat_id: int, *, before_id: int | None,
                           after_id: int | None, limit: int) -> MessagePage               # NotFound

    # запись пользователем
    async def send_message(self, user_id: int, chat_id: int, text: str,
                           client_msg_id: UUID | None) -> MessageOut  # NotFound, Validation, RateLimited
    async def mark_read(self, user_id: int, chat_id: int, message_id: int) -> None  # NotFound, Validation

    # для matching
    async def ensure_direct_chat(self, user_a: int, user_b: int) -> int   # chat_id; a == b → DomainValidation cannot_chat_self
    # для groups
    async def create_group_chat(self, group_id: int, member_ids: Sequence[int]) -> int
    async def add_group_member(self, group_id: int, user_id: int) -> int  # chat_id, идемпотентно
    async def remove_group_member(self, group_id: int, user_id: int) -> None  # идемпотентно
```

Побочные эффекты и события. Все события кладутся в `ChatEventSink` и уходят клиентам только после commit:

| Метод | Изменение БД | Событие |
|---|---|---|
| `send_message` (новое) | INSERT `messages`; `last_read` отправителя = id нового сообщения | `MessageCreated(message, recipient_ids = все участники)` |
| `send_message` (повтор `client_msg_id`) | — | нет; возвращается существующее сообщение |
| `mark_read` | UPDATE `chat_members.last_read_message_id` (только вперёд) | `ReadUpdated(user_id, chat_id, message_id)` |
| `ensure_direct_chat` (новый) | INSERT `chats` с `direct_key = "<min>:<max>"`, два `chat_members` | `ChatCreated(chat_id, user_ids = [a, b])` |
| `ensure_direct_chat` (существует) | — | нет |
| `create_group_chat` | INSERT `chats(type=group, group_id)`, участники | `ChatCreated(chat_id, member_ids)` |
| `add_group_member` (новый участник) | INSERT `chat_members` | `ChatCreated(chat_id, [user_id])` |
| `remove_group_member` (был участником) | DELETE `chat_members` | `ChatRemoved(chat_id, [user_id])` |

Rate limit: `limiter.check(user_id)` перед вставкой; по умолчанию 5 сообщений/с, всплеск 10, общий для REST и WS. Повтор с тем же `client_msg_id` в лимит не засчитывается — проверка дубля идёт до лимитера.

### `ChatRepository` (Protocol в `repositories/interfaces.py`)

Возвращает модели или frozen-dataclass «представления» (`UserBriefView`, `MessageView`, `ChatSummaryView`), объявленные там же. Схемы Pydantic репозиторий не возвращает.

```python
async def get_user_chat(self, user_id: int, chat_id: int) -> ChatSummaryView | None   # None, если не участник
async def list_user_chats(self, user_id: int, limit: int) -> Sequence[ChatSummaryView]
async def is_member(self, chat_id: int, user_id: int) -> bool
async def list_member_ids(self, chat_id: int) -> Sequence[int]

async def get_or_create_direct(self, key: str, member_ids: tuple[int, int]) -> tuple[Chat, bool]
async def add_group_chat(self, group_id: int) -> Chat
async def get_by_group(self, group_id: int) -> Chat | None
async def add_members(self, chat_id: int, user_ids: Sequence[int]) -> Sequence[int]   # реально добавленные
async def remove_member(self, chat_id: int, user_id: int) -> bool

async def find_by_client_id(self, sender_id: int, client_msg_id: UUID) -> MessageView | None
async def add_message(self, chat_id: int, sender_id: int, text: str,
                      client_msg_id: UUID | None) -> tuple[MessageView, bool]       # bool = создано
async def list_messages(self, chat_id: int, *, before_id: int | None,
                        after_id: int | None, limit: int) -> Sequence[MessageView]  # по возрастанию id
async def message_in_chat(self, chat_id: int, message_id: int) -> bool
async def advance_read(self, chat_id: int, user_id: int, message_id: int) -> None
```

Реализация в SQL:
- `get_or_create_direct`: `INSERT … ON CONFLICT (direct_key) DO NOTHING RETURNING id`; если ничего не вернулось — `SELECT`. Участники вставляются с `ON CONFLICT DO NOTHING`.
- `add_message`: `INSERT … ON CONFLICT (sender_id, client_msg_id) DO NOTHING RETURNING …`, при конфликте — `SELECT` существующего. Так закрывается гонка двух одинаковых `send`.
- `list_user_chats` / `get_user_chat`: одним запросом. `chat_members` текущего пользователя → `LEFT JOIN LATERAL` последнего сообщения с именем отправителя → скалярный подзапрос `unread` → для direct `LEFT JOIN` второго участника и `users.name`, для group — `groups.name`. Это разрешённое read-only исключение, по аналогии с `SearchRepository` в общей архитектуре.
- Ни один метод не вызывает `commit`. `flush` вызывается там, где нужен `id`.

### Unit of work, outbox и Notifier

```python
# services/notifier.py
@dataclass(frozen=True) class MessageCreated:  message: MessageOut; recipient_ids: tuple[int, ...]
@dataclass(frozen=True) class ReadUpdated:     user_id: int; chat_id: int; message_id: int
@dataclass(frozen=True) class ChatCreated:     chat_id: int; user_ids: tuple[int, ...]
@dataclass(frozen=True) class ChatRemoved:     chat_id: int; user_ids: tuple[int, ...]
ChatEvent = MessageCreated | ReadUpdated | ChatCreated | ChatRemoved

class ChatEventSink(Protocol):
    def add(self, event: ChatEvent) -> None: ...

class Notifier(Protocol):
    def publish(self, event: ChatEvent) -> None: ...   # синхронно, не блокирует, не бросает
```

```python
# core/db.py
@asynccontextmanager
async def session_scope(notifier: Notifier | None = None) -> AsyncIterator[AsyncSession]:
    """Единственное место commit/rollback. Используется get_session (HTTP) и WS (на каждый фрейм)."""

def get_outbox(session: AsyncSession) -> ChatEventSink:
    """Outbox, привязанный к session.info; создаётся один раз на сессию."""
```

- `get_session` переписывается поверх `session_scope`. Поведение для HTTP не меняется: commit до ответа (`scope="function"`), rollback при исключении.
- Outbox хранит события в `session.info["outbox"]`.
- Глобальные слушатели SQLAlchemy на `Session` вызывают `notifier.publish(e)` для каждого события:
  - `after_commit` — только для внешней транзакции, вложенные savepoint'ы не считаются;
  - `after_rollback` очищает список.
- `publish` синхронный, потому что `ConnectionManager` только делает `put_nowait` в очереди соединений. Вызов из синхронного SQLAlchemy-хука безопасен. Исключение внутри `publish` перехватывается и логируется без payload, commit оно не ломает.
- `Notifier` для HTTP-запроса — `request.app.state.connections` (DI `get_notifier(conn: HTTPConnection)`). Notifier привязывается к сессии в `get_session`.

### ConnectionManager (`api/connections.py`)

```python
class Connection:  # одно WS-соединение
    user_id: int
    queue: asyncio.Queue[str]            # готовый JSON; maxsize = ws_send_queue_size (100)
    writer: asyncio.Task[None]           # единственный, кто пишет в сокет

class ConnectionManager:  # implements Notifier
    def register(self, conn: Connection) -> None     # вытесняет самое старое сверх лимита (4000)
    def unregister(self, conn: Connection) -> None
    def send_to(self, conn: Connection, frame: ServerFrame) -> None   # ack / error / pong
    def publish(self, event: ChatEvent) -> None      # событие → фрейм → всем соединениям получателей
```

- Фрейм сериализуется **один раз на событие**, а не на соединение.
- Переполнение очереди → соединение помечается на закрытие `1013`, событие для него отбрасывается.
- В сокет пишет только `writer`: ответы на собственные фреймы (`ack`, `error`, `pong`) идут через ту же очередь. Конкурентных `send_text` нет.
- Экземпляр создаётся в `create_app` и кладётся в `app.state.connections`. Тесты получают свежий менеджер на каждое приложение.

### WS-эндпоинт (`api/ws.py`)

```python
@router.websocket("/ws")
async def chat_ws(websocket: WebSocket, units: ChatUnitFactoryDep, settings: SettingsDep) -> None
```

`ChatUnitFactory` — зависимость уровня соединения, **не** зависящая от `SessionDep`. Метод `async with units.chat_service() as service:` открывает `session_scope(notifier)`, собирает репозиторий, outbox, лимитер и `ChatService`, а на выходе коммитит. Для auth есть отдельный `units.authenticate(token) -> User`. В API-тестах фабрика подменяется версией на фейках.

Цикл: `accept` → `auth` с таймаутом → `register` → читать фреймы с idle-таймаутом → валидировать через `TypeAdapter(ClientFrame)` → `send`/`read` выполняются в новом `chat_service()` scope. После выхода из scope commit уже случился и outbox разослал события, затем `ack` → `finally: unregister`, отмена `writer`.

`DomainError` → фрейм `error` с `code`. Неизвестное исключение → лог без payload и фрейм `internal_error`, соединение живёт.

## Data flow

1. **Подключение.** Веб открывает `/api/v1/ws` и шлёт `auth`. Эндпоинт через `units.authenticate` (своя короткая сессия) проверяет токен, пользователя и анкету → `auth_ok` → `ConnectionManager.register`.
2. **Отправка через WS.** `send{chat_id, text, client_msg_id}` → `chat_service()` scope → `is_member`, иначе `chat_not_found` → `find_by_client_id`; если найдено, сразу `ack` с существующим сообщением → валидация текста → `limiter.check` → `add_message` → `advance_read` отправителя → `outbox.add(MessageCreated(recipients = list_member_ids))` → выход из scope: commit → `after_commit` → `ConnectionManager.publish` раскладывает фрейм `message` по очередям всех онлайн-соединений участников → эндпоинт кладёт `ack` в очередь своего соединения → writer'ы отправляют.
3. **Отправка через REST.** `POST /chats/{id}/messages` → тот же `ChatService.send_message` в сессии запроса → `get_session` коммитит → события уходят в WS-соединения участников → `201 MessageOut`.
4. **Ошибка в транзакции.** Исключение после `outbox.add` → rollback → `after_rollback` очищает outbox → никто не получает фантомное сообщение.
5. **Прочтение.** `read{chat_id, message_id}` (или REST) → членство → `message_in_chat` → `advance_read` → `ReadUpdated` → commit → фрейм `read` во все вкладки читателя, и веб обнуляет счётчик.
6. **Переподключение.** Веб переподключается с экспоненциальной задержкой. Для открытого чата он вызывает `GET /chats/{id}/messages?after_id=<последний известный>` до `has_more = false`, а для списка — `GET /chats`.
7. **Взаимный матч** (контекст matching, когда появится). `MatchingService.like` → `ChatService.ensure_direct_chat(a, b)` в той же сессии → commit → `chat_created` обоим онлайн → веб вызывает `GET /chats/{chat_id}`.
8. **Группа.** `approve` → `create_group_chat(group_id, [author])`. `join` → `add_group_member` → `chat_created` вступившему. `leave` → `remove_group_member` → `chat_removed`. Новые события вышедшему не приходят, потому что получатели вычисляются из `chat_members` на каждое событие.

## Decisions

1. **Outbox на хуках `after_commit`/`after_rollback` SQLAlchemy, а не буфер в зависимости FastAPI.** Teardown зависимостей идёт в обратном порядке, поэтому буфер, зависящий от сессии, сработал бы *до* commit в `get_session`. Хук срабатывает ровно после реального commit одинаково для HTTP и WS, а `commit` остаётся в одном месте (`session_scope`).
2. **`Notifier.publish` синхронный и неблокирующий.** Каждое соединение имеет ограниченную очередь и единственный task-писатель. Так медленный клиент не тормозит отправителя и других, конкурентной записи в сокет нет, вызов из синхронного хука безопасен.
3. **Получатели вычисляются из БД в момент события** (`list_member_ids` внутри той же транзакции). Нет кэша членства в памяти, а значит, нет его инвалидации при вступлении или выходе из группы.
4. **Одна сессия на каждый WS-фрейм** через фабрику `ChatUnitFactory`, а не `Depends(get_session)` на соединение. Это следует из `CLAUDE.md`: соединение живёт долго, сессию держать нельзя. Фабрика заодно делает WS тестируемым на фейках.
5. **Идемпотентность через `messages.client_msg_id`** и UNIQUE `(sender_id, client_msg_id)`. Повтор после обрыва не создаёт дубль и не рассылается повторно. В WS поле обязательно, в REST — опционально.
6. **Отправка и через WS, и через REST** — одним методом сервиса. REST даёт типизированный из OpenAPI путь и удобен в тестах и для fallback. WS остаётся основным путём для отправки и единственным для доставки.
7. **История всегда по возрастанию id с курсорами `before_id`/`after_id`.** Порядок сообщений — по `id`, а не по `created_at`, потому что `id` уникален и монотонен в пределах коммитов одного чата для одного писателя. `after_id` закрывает догрузку после переподключения без отдельного протокола «resume» в WS.
8. **`chat_created` несёт только `chat_id`.** `ChatOut` зависит от получателя (`title`/`peer` у direct разные), а собирать его без БД в менеджере нельзя. Клиент делает один `GET /chats/{id}`.
9. **Отправка своего сообщения двигает свой `last_read`.** Своё сообщение не считается непрочитанным (как в Telegram). Формула unread дополнительно исключает `sender_id = me` на случай отправки из другой вкладки.
10. **Унифицированный код «нет доступа» — `chat_not_found`** и в REST, и в WS: существование чужого чата не раскрывается.
11. **Auth первым фреймом, без токена в URL.** Это CSRF-безопасно для WS: браузер не подставляет токен сам, cookie не используются, поэтому проверка `Origin` не обязательна. Способ нужно подтвердить с вебом (см. Open questions).
12. **Rate limit в сервисе через `RateLimiter`** (в памяти, по `user_id`), общий для REST и WS. Решение 15 общей архитектуры («10 сообщений/с на соединение») заменено на 5/с со всплеском 10 **на пользователя**: лимит на соединение обходится открытием вкладок.
13. **Тайминги и лимиты в `Settings`:** `ws_auth_timeout_seconds = 5`, `ws_idle_timeout_seconds = 60`, `ws_max_connections_per_user = 5`, `ws_send_queue_size = 100`, `chat_rate_per_second = 5`, `chat_rate_burst = 10`. В тестах таймауты уменьшаются.
14. **Логи:** connect/disconnect с `user_id` и кодом закрытия, ошибки обработки фреймов с `type` и `code`. Текст сообщений, токены и имена не логируются никогда, в том числе в `internal_error`.

### Схема БД (дельта к текущим моделям)

| Таблица | Изменение | Зачем |
|---|---|---|
| `messages` | `+ client_msg_id uuid NULL`, UNIQUE (`sender_id`, `client_msg_id`) | идемпотентность (решение 5); NULL не конфликтуют |
| `chat_members` | индекс `(user_id)` | PK `(chat_id, user_id)` не помогает выборке «мои чаты» и проверке членства |

Индекс `messages (chat_id, id)` уже есть и покрывает историю, последний месседж и unread.

Сознательно **не** делается:
- `messages.id` остаётся `Integer`, потому что на масштабе одной компании запаса в 2 млрд хватает;
- CHECK на `direct_key` и на непустой текст не добавляются: инварианты держит `ChatService` (`ensure_direct_chat` всегда ставит ключ, `send_message` отбрасывает пустой текст) и покрывают его unit-тесты.

`last_read_message_id` остаётся без FK: это просто граница, а не ссылка.

Миграция чата создаётся **поверх** базовой миграции каркаса (`down_revision` = её ревизия). Если базовой ещё нет, модели правятся в этой ветке, а автогенерация делается после появления базовой, чтобы не получить две головы Alembic. После изменения схемы нужно обновить раздел «Основные сущности» в `CLAUDE.md` (поле `client_msg_id`).

### Изменения относительно общей архитектуры

| Было в `backend-architecture` | Стало | Почему |
|---|---|---|
| WS `error not_member` | `error chat_not_found` | единообразно с REST 404 (решение 10) |
| `chat_created {chat: {...}}` | `chat_created {chat_id}` | решение 8 |
| per-request буфер, отправка после `commit` в `get_session` | outbox на хуках сессии | решение 1 |
| rate limit 10/с на соединение | 5/с со всплеском 10 на пользователя | решение 12 |
| `POST /chats/{id}/read {message_id}` и `GET /chats` | + `GET /chats/{id}`, `POST /chats/{id}/messages`, `after_id`, `has_more` | догрузка после переподключения, REST-отправка |
| `ensure_direct_chat(...) -> Chat`, `add_member(chat_id, ...)` | `-> int`, `add_group_member(group_id, ...)` | groups не должен знать `chat_id` своей группы |

## Verification boundaries

- **ChatService (unit, фейковые репозиторий, sink и лимитер):**
  - не участник и несуществующий чат дают один и тот же `chat_not_found`, для `get_chat`, `get_messages`, `send_message`, `mark_read`;
  - текст `"   "` → `message_empty`; 4001 символ после strip → `message_too_long`; ровно 4000 проходит; пробелы по краям обрезаются в сохранённом тексте;
  - повторный `send_message` с тем же `client_msg_id` возвращает то же сообщение, в репозитории одна строка, в sink одно событие, лимитер вызван один раз;
  - `MessageCreated.recipient_ids` содержит всех участников, включая отправителя; `last_read` отправителя равен id нового сообщения;
  - `ensure_direct_chat(2, 1)` и `(1, 2)` дают один чат с ключом `"1:2"`; второй вызов не публикует событие; `(1, 1)` → `cannot_chat_self`;
  - `mark_read` чужого сообщения → `message_not_in_chat`; отметка более старого id не уменьшает указатель;
  - `add_group_member` повторно не создаёт событие; `remove_group_member` несостоящего — no-op без события;
  - лимитер отказал → `RateLimitedError`, ничего не записано.
- **Outbox (интеграционно на тестовой PostgreSQL):** события из сессии доходят до фейкового `Notifier` только после commit; после rollback — ни одного; исключение в `publish` не ломает commit.
- **ChatRepository (тестовая PostgreSQL):**
  - два параллельных `get_or_create_direct` с одним ключом в двух сессиях дают один чат;
  - два параллельных `add_message` с одним `client_msg_id` дают одну строку и один `created = True`;
  - вторая вставка с тем же (`sender_id`, `client_msg_id`) отклоняется UNIQUE, а с `client_msg_id = NULL` — нет;
  - `unread_count` учитывает `last_read` и исключает свои сообщения;
  - курсоры `before_id`/`after_id` и `has_more` на границах (ровно `limit` строк, пустой чат);
  - сортировка `list_user_chats`, `title`/`peer` для direct и group.
- **ConnectionManager (unit, фейковые сокеты):** событие доходит во все соединения всех получателей и не доходит до чужих; сериализация один раз на событие; переполнение очереди закрывает только медленное соединение кодом `1013`; шестое соединение вытесняет самое старое кодом `4000`; `unregister` идемпотентен.
- **REST (httpx + фейки, по образцу `tests/api/`):** каждая ручка без токена → `401`, без анкеты → `403 survey_required`; чужой чат → `404 chat_not_found`; оба курсора → `422`; `POST messages` → `201` и формат `MessageOut`; `429` в едином формате ошибок.
- **WebSocket (`starlette.testclient.TestClient.websocket_connect`, фабрика на фейках):**
  - нет `auth` за таймаут → закрытие `4401`; неверный токен → `4401`; без анкеты → `4403`;
  - A и B в чате: `send` от A → B получает `message`, A получает `ack` и `message` с тем же `id`;
  - `send` в чужой чат → `error chat_not_found` с `client_msg_id`, в фейке нет сообщения, B ничего не получил;
  - невалидный JSON или неизвестный `type` → `error bad_frame`, соединение живо, следующий `ping` → `pong`;
  - `read` → фрейм `read` во второе соединение того же пользователя.
- **OpenAPI:** `/openapi.json` содержит `ClientFrame`, `ServerFrame`, `ChatOut`, `MessageOut`, `MessagePage`, ошибки чата перечислены в `responses`.
- **Логи (caplog):** после `send` через REST и WS в логах нет текста сообщения и токена.
- **Слои (ревью по чек-листу `CONTRIBUTING.md`):** в `services/chat.py` нет `sqlalchemy`, в репозитории нет `commit`, в `api/ws.py` и `api/chats.py` нет SQL.

## Risks

| Риск | Митигация |
|---|---|
| In-memory `ConnectionManager` не работает при нескольких воркерах | Один воркер (решение 1 общей архитектуры), явный `--workers 1` в Dockerfile; доставка спрятана за `Notifier` — замена на Postgres `LISTEN/NOTIFY` не трогает сервис и outbox |
| Событие уходит до commit или теряется при rollback | Outbox на `after_commit`/`after_rollback`; интеграционный тест rollback |
| Сбой процесса между commit и доставкой: онлайн-клиент не получил `message` | Сообщение уже в БД; клиент догружает по `after_id` при переподключении и при возврате фокуса на вкладку |
| Параллельные коммиты в один чат: id 11 видим раньше id 10, а `after_id = 11` пропустит 10 | Реалтайм доставляет оба; для догрузки окно — миллисекунды. Если проявится — веб догружает с `after_id = last_id - 50` и дедуплицирует по id |
| Медленный клиент раздувает память | Ограниченная очередь (100) и закрытие `1013` |
| Много вкладок или ботов открывают соединения | Лимит 5 соединений на пользователя, вытеснение старых |
| Спам сообщениями | Rate limit на пользователя, общий для REST и WS |
| Гонка двух `ensure_direct_chat` или двух одинаковых `send` | `ON CONFLICT DO NOTHING` + повторный SELECT по UNIQUE |
| Две головы Alembic из-за параллельной работы над каркасом | Миграция чата — после базовой, `down_revision` на неё; при конфликте `alembic merge heads` |
| Matching и groups ещё не готовы: нечем создать чат для ручной проверки | Тесты создают чаты через сервис; для веба — сид демо-пар с готовым личным и групповым чатом (только вымышленные данные) |
| Открытый вопрос «писать без матча» решат иначе | Создание личного чата сосредоточено в `ensure_direct_chat`; новый `POST /chats/direct {user_id}` — одна ручка поверх него |

## Open questions

| Вопрос | Владелец | Текущее допущение |
|---|---|---|
| Можно ли писать человеку до взаимного лайка | команда + заказчик | нет: личный чат создаёт только matching |
| Способ передачи JWT в WS | команда бэка + веб | первый фрейм `auth` (решение 11) — подтвердить с вебом до реализации WS |
| Нужны ли read receipts («прочитано собеседником») | команда | нет в MVP; `last_read_message_id` уже хранится, добавляется событием без изменения схемы |
| Редактирование и удаление сообщений | команда | нет в MVP |
| Хранение истории (срок, удаление при увольнении сотрудника) | заказчик | бессрочно; `sender_id` без каскада, удаление пользователя пока не предусмотрено |

## Hand-off

Для планирования выполнения нужны этот документ, `CLAUDE.md`, `CONTRIBUTING.md` (порядок слоёв и чек-лист) и общая архитектура (контракты identity). Ветка — `feature/chat-websocket`.

Предлагаемые фазы:

1. **Схема:** дельта моделей в `app/models/chat.py`, миграция поверх базовой (или отложенная автогенерация), обновление `CLAUDE.md`.
2. **Ядро:** `RateLimitedError` и его маппинг; `session_scope`, outbox и хуки в `core/db.py`; `core/rate_limit.py`; новые поля `Settings`; `services/notifier.py`.
3. **Репозиторий:** Protocol и view-dataclass'ы, `SqlChatRepository`, `FakeChatRepository`, тесты на PostgreSQL.
4. **Сервис:** `ChatService` и unit-тесты на фейках.
5. **REST:** `schemas/chat.py`, `api/chats.py`, DI (`get_chat_repository`, `get_notifier`, `get_chat_service`), подключение в `main.py`, API-тесты.
6. **Реалтайм:** `schemas/ws.py` и OpenAPI-компоненты, `api/connections.py`, `ChatUnitFactory`, `api/ws.py`, тесты менеджера и WS.
7. **Интеграция и демо:** проверить внутренний контракт для matching и groups, сид демо-чатов, ручная проверка двумя вкладками.

Зависимости: 1 → 3; 2 → 4; 3 + 4 → 5 → 6. Фазы 1 и 2 независимы, поэтому их могут параллельно делать разные люди. Фаза 6 начинается после подтверждения способа auth с вебом.
