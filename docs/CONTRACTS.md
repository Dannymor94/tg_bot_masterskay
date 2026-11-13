# CONTRACTS — границы модулей для параллельных агентов

**Статус:** согласование интерфейсов до кода. Конкретные структуры ответов YCLIENTS будут подтверждены только T-103; здесь нет придуманных полей внешнего API.

## Доменные функции (чистые)

| Функция | Вход → выход | Инвариант |
|---|---|---|
| `diff(prev, cur)` | Нормализованные снимки → доменные события | Повторный снимок: ноль событий |
| `rules(event, cfg)` | Событие + правила → описания задач/сообщений | Без IO, однозначные ключи |
| `occupancy(records, activities, halls)` | Записи + события + конфиг залов → занятые интервалы | И аренда, и групповое событие занимают зал |
| `empty_windows(weeks, cfg)` | Историческая занятость → окна | 8 недель и порог из `studio.yaml` |
| `free_slots(occupancy, days)` | Занятость → доступные интервалы | Не выдавать занятые |
| `price(hall, dt)` | Зал + локальные дата/время → цена | Только правила YAML |
| `match_phone(phone, candidates)` | Номер + кандидаты → решение привязки | 0/1/2+ совпадений различаются |
| `segment(users, name)` | Пользователи + сегмент → Telegram ID | `news_opt_in` для анонсов |
| `can(user, action, target)` | Субъект + операция + объект → bool | Default deny, проверяется в сервисе |

T-101 фиксирует общие Pydantic DTO в `app/domain/models.py`: `RecordSnapshot`, `DomainEvent`, `TaskDraft`, `OutboxDraft`, `User`, `Interval`. Это внутренние модели, не схема внешнего API. Все интервалы полуоткрытые `[starts_at, ends_at)`, время aware; в SQLite сохраняется UTC. Owner — флаг пользователя, роль — client/teacher/admin. Строковые коды залов берутся из YAML.

Зафиксированные границы для следующих задач:

- T-102 возвращает обезличенные JSON-ответы операций API_MATRIX; преобразование в `RecordSnapshot` делает T-104 после fixtures T-103.
- `diff(prev: Mapping[int, RecordSnapshot], cur: Mapping[int, RecordSnapshot], occurrences: Mapping[int, int]) -> list[DomainEvent]`; `occurrences` передаёт следующий локальный номер перехода для каждой сравниваемой записи по контракту ниже. Время события берётся из `seen_at` текущего снимка.
- `rules(event: DomainEvent, cfg: StudioConfig) -> tuple[list[TaskDraft], list[OutboxDraft]]`.
- `can(user: User, action: str, target: RecordSnapshot | User | None) -> bool`.
- `segment(users: Sequence[User], name: str) -> list[int]`.
- T-105 принимает `OutboxDraft`, использует `outbox` и `kv` из `app/db/models.py`; создание задачи/события/сообщения выполняется одной транзакцией вызывающего сервиса.

DTO расписания, занятости и привязки телефона уточняются в своих задачах по подтверждённым fixtures; T-101 не вводит неподтверждённые поля. Модели БД — SQLAlchemy Core `Table`, общий `metadata`; async engine создаётся через `app.db.engine.create_db`.

## Дедупликация наблюдаемых переходов (контракт T-104)

`occurrence` — монотонный локальный номер принятого изменения записи, а не поле YCLIENTS. Он хранится десятичной строкой в `kv` под ключом `sync:transition_seq:<record_id>`. Для впервые наблюдаемой записи предыдущее значение равно 0. Счётчик сохраняется при отмене/удалении и не очищается вместе с историей событий. Потеря существующего счётчика — ошибка целостности, а не основание начать нумерацию заново.

1. Сервис получает наблюдение API вне транзакции, затем открывает SQLite `BEGIN IMMEDIATE`. Внутри транзакции он читает актуальные `record_snapshots` и счётчики; сравнение с ранее прочитанным вне транзакции снимком запрещено. Это сериализует конкурирующих писателей.
2. Сервис передаёт чистой `diff()` снимки и `occurrences[record_id] = сохранённый_номер + 1`. Функция не обращается к БД, часам или генератору случайных значений. Одни и те же входы дают одинаковые события и ключи. Равенство бизнес-состояния исключает `seen_at` и локальный номер: повторное наблюдение неизменного состояния не создаёт событий и не увеличивает счётчик. `content_hash` должен отражать нормализованное бизнес-состояние, включая признак удаления.
3. Для каждого принятого изменения сервис сохраняет новый номер и новый снимок, вставляет события, задачи и outbox, полученные из `rules()`, **одной транзакцией**. При отсутствии изменения обновить `seen_at` допустимо без увеличения номера. Подтверждённое удаление представляется снимком с `deleted=True`; исчезновение из окна опроса само по себе переходом не является. Решение о подтверждении удаления остаётся T-104.
4. `DomainEvent.dedupe_key` имеет формат `record:<record_id>:transition:<occurrence>:<event_type>`. На одну запись/номер допускается не более одного события каждого типа. Ключи задач и outbox включают этот ключ события и устойчивый идентификатор конкретного эффекта: для задачи — правило и логическую цель, для сообщения — правило, получателя и логическое назначение сообщения. Составные ключи эффектов кодируются JSON-массивом строк (`ensure_ascii=True`, `separators=(",", ":")`), чтобы исключить неоднозначность разделителей. Ни `seen_at`, ни текущее время, ни случайный UUID в идентичность эффекта не входят.
5. Сбой до commit откатывает снимки, счётчики, события и эффекты; повтор с тем же наблюдением получает тот же номер и те же ключи. После commit повтор видит уже сохранённое состояние и не создаёт событий. Outbox sender видит только закоммиченные сообщения. Уникальные ключи остаются дополнительной защитой; конфликт с несовпадающим содержимым — ошибка, а не тихое подавление события.

Например, при сохранённых состоянии A и номере 7 последовательность наблюдений B, A, B получает номера 8, 9, 10. Оба перехода A→B имеют разные ключи (`transition:8` и `transition:10`), хотя пары хешей совпадают. Повтор B после последнего commit оставляет номер 10 и создаёт ноль событий. Ретрай откатившегося последнего перехода снова использует 10. Гарантия относится к наблюдаемым состояниям: переходы между опросами без наблюдения восстановить нельзя.

T-101 фиксирует только этот контракт и использует существующую `kv`, без изменения схемы БД. Реализация протокола и тесты A→B→A→B, повторного наблюдения, rollback/retry и конкурирующих писателей входят в T-104; доставка outbox — в T-105.

## Адаптер YCLIENTS

Операции, пути, методы и статусы брать **только** из `docs/API_MATRIX.yaml`; `kind: read` не означает обязательно HTTP GET. `pending` недоступен обычному приложению. T-103 сохраняет только обезличенные fixtures и явный статус verified/unavailable. На unavailable применяется записанный fallback; без тихой подмены полей.

## T-102: YCLIENTS read-only adapter and verified normalization

`app/integrations/yclients.py` contains an opt-in read-only HTTP client.
Every operation must be `kind: read`, have a locally allowed
operation-ID + METHOD + exact path template in the **code-reviewed**
`SAFE_READ_ROUTES`, and be marked `verified` in `docs/API_MATRIX.yaml`.
Unverified operations remain unavailable to normal runtime; the explicit
owner-only `allow_pending` switch can only try routes already in this
static allowlist. No writable endpoint is authorized by metadata alone.

`YclientsClient.request()` and `paginate()` return only **diagnostic**
sanitized values (numeric IDs/bools/null and permitted structural labels;
all other scalar values are masked; unknown dynamic keys are dropped).
They must never feed business normalization.

`request_normalized(..., normalize=...)` and
`paginate_normalized(..., decode_page=..., normalize_item=...)` are
trusted in-memory DTO boundaries; decoders return `pydantic.BaseModel`
instances based on **verified T-103 fixtures**. Raw JSON is temporary,
private and never logged, returned wholesale or stored. Error messages
never expose the raw response.

Pagination has an explicit decoder to determine `has_more` rather than
guessing external YCLIENTS fields. Every page is bounded and a repeated
non-empty page (even with modified completion flag), a progressless
empty intermediate page, invalid decoder output, retry exhaustion or
max-pages exhaustion fails the entire request. Identical-page detection
alone does **not** prove unique IDs, partial overlap or historical
coverage; T-103/T-104 must enforce those verified invariants before
synchronization.

Only code-reviewed routes can be promoted to verified; an endpoint
discovered during T-103 that differs from the static list requires a
reviewed code + test change first. T-103 may delete remaining candidates
after it records an approved `path`; the allowlist remains authoritative.

## Побочные эффекты

`services` не отправляет Telegram напрямую: создаёт запись `outbox` с `dedupe_key` в той же транзакции, что задача/событие. `outbox sender` доставляет, обрабатывает 429/backoff и сохраняет состояние. Подтверждённая доставка не равна выполнению задачи; повторная обработка не создаёт второй outbox record.

## Разделение файлов между агентами

- **T-101:** фундамент, модели БД, миграция и общие DTO. Только после этого фиксировать контракты.
- **T-102:** `app/integrations/yclients.py` и его тесты, не менять миграцию без отдельного согласования.
- **T-105:** `app/jobs/`, сервис outbox/scheduler и тесты, не менять API adapter.
- **T-104:** `app/domain/` и sync после T-103.
- **T-201+:** сервисы/handlers по отдельным задачам; общий файл — один писатель.

Одно рабочее дерево — один агент. Параллельный PR не должен редактировать `PLAN.md` одновременно с другим: каждый коммит задачи обновляет свою строку в отдельной ветке, интегратор последовательно сверяет и разрешает конфликты. Merge только владелец.

## Приёмка

Отдельные тесты на сценарии из `PLAN.md`, независимо выполненный `make check`, полный diff и вердикт ревьюера. При отсутствии доказательств — BLOCKED, а не APPROVE.


## Кодовые заготовки

`scaffolds/` хранит самодостаточные проверяемые примеры **внутренних** моделей и чистых функций. Не импортировать их из работающего приложения напрямую: перенести/адаптировать с тестами после фиксации DTO в T-101. Не переносить выдуманные поля YCLIENTS в интеграцию. Статус и критерии задач определяются только `PLAN.md`, а не наличием модуля в `scaffolds/`.

## RBAC T-201

`User.access_revoked` / `users.access_revoked` — отдельный запрет доступа,
не `blocked_at` (транспортная блокировка Telegram). Отозванный пользователь
не может восстановить права сохранённым приглашением. Owner — только admin
с `is_owner`; его ID закрепляется bootstrap в `kv`, смена конфигурации на другой
ID требует отдельного решения владельца.

Матрица действий T-201 (все прочие действия и цели запрещены):

| Action | Субъект | Цель |
|---|---|---|
| `menu` | привязанный client/teacher, admin, owner | сам пользователь |
| `invite.admin` | owner | нет |
| `invite.teacher` | admin, owner | нет |
| `access.revoke` | owner | другой admin/teacher, не owner |
| `invite.redeem` | client/teacher, в том числе ещё не привязанный | сам пользователь |

`invite.redeem` — только вход в протокол приглашения, без бизнес-привилегий.
Сервис дополнительно проверяет токен, срок, создателя и актуальные права.
Записи/занятия и бизнес-действия вводятся только в соответствующих задачах.

Приглашения — bearer-ссылки: получатель определяется **только** Telegram
`from_user.id` в личном чате, не ID в ссылке/команде. Передача ссылки другому
человеку даёт ему возможность первым её погасить; привязки к заранее известному
получателю в действующей таблице `invites` нет. Создатель не может погасить свою
ссылку. Актуальные права создателя перепроверяются при погашении; его отзыв
аннулирует ещё не использованные ссылки. Отзыв самого получателя нельзя обойти
погашением другой ссылки.

В `invites` сохраняется только SHA-256 токена. В outbox — случайный nonce,
роль и адресат-создатель. Sender восстанавливает 43-символьный токен HMAC-SHA256
(отдельный домен `studio-bot:invite:v1:`) с ключом Telegram-бота только в памяти.
Nonce и дампа БД недостаточно для получения токена без внешнего секрета.
Это позволяет повторить доставку после рестарта, не сохраняя токен ни в outbox,
ни в аудите/логах. Ротация токена бота инвалидирует ожидающую доставку старых
приглашений; их нужно выдать заново. Сроки обеих ролей заданы в YAML;
преподаватель — строго 24 часа. Погашение выдаёт ему только неподтверждённую
роль teacher (`teacher_kind=None`), без доступа до T-401/T-301.

Команды T-201: `/access`, `/invite admin|teacher`, `/accept <token>`,
`/start invite_<token>`, `/revoke <telegram_id>`. Обычный `/start` остаётся
T-301. Недопустимые команды не дают эффектов и не отражают ввод в ответе.
`actor_id` сервисов — доверенный вход транспорта: adapters обязаны брать его
из аутентифицированного события Telegram; параметры команд могут задавать
только цель отзыва, роль приглашения и сам bearer-токен. Группы, сообщения
ботов и несовпадение chat ID / from_user ID отклоняются.

Все записи RBAC сериализованы `BEGIN IMMEDIATE`: чтение прав, изменение роли,
погашение, аудит и outbox принадлежат одной транзакции. Повтор выдачи защищён
UNIQUE outbox `dedupe_key` (Telegram ID, message ID, действие); повтор отзыва —
флагом пользователя. Отзыв необратим через интерфейс T-201: процедуры
восстановления/смены владельца не определены ADR и не добавлены.
Погашение всегда проверяет текущие права создателя. Sender/enqueue проверяют
также `access_revoked`; ранее поставленные pending/summary отменяются при
отзыве. Уже принятая Telegram отправка необратима; гарантии T-105 сохранены.

## T-203 task queue

`app.domain.tasks.rule(code, source_key, cfg, now, ...)` produces internal
`TaskDraft` values for R1–R6/R8. `source_key` identifies the originating action
or event; `now` is its stable timestamp on replay. R2 requires aware `starts_at`;
R5 uses the configured score threshold; R6 uses top_windows_in_report ranking.
Future source adapters provide text/candidate identifiers without YCLIENTS names
or phones. These functions do not define external API fields. R4 body may hold
the temporary own-contact input; ownership verification and binding retry belong
to T-301. Unresolved R4 stays open: generic Take/Done return False. Take
queues a current card explaining Retry linking. Only verified binding closes R4
and clears its body atomically; the phone never enters outbox or audit (Issue #14,
ADR-06/09, owner-approved integration flow).

`create_task(db, draft, cfg, now)` is a trusted internal source boundary, called
inside `BEGIN IMMEDIATE` together with the source event. It is not exposed to
Telegram. Replaying an open task with different content raises an error. Replays
never restore redacted R4 or completed/expired content. Task rows and UNIQUE
keys remain as TTL tombstones; audit records are retained. A SHA-256 draft
identity in kv preserves content-conflict detection after TTL. due_at is
normalized to UTC before hashing; R4 body is excluded so no phone-derived hash
survives its allowed lifetime. R4 body conflicts are checked only while open.

Public `TaskService` operations reload the actor within the write transaction:
`manual` (`tasks.create`), `queue` (`tasks.list`), `take` (`tasks.take`), `finish`
(`tasks.done` / `tasks.reply`), `reply_prompt` (`tasks.reply`). Only current
admin/owner users are allowed. For non-R4 tasks, Take requires `open`; Done/Reply require `taken`
and the assignee or owner. Reply is R3-only, with one outbox response and task
completion in the same transaction. Repeated transitions produce no effects.

`/tasks`, `/task текст`, `/take ID`, `/done ID`, `/reply ID текст`, and inline
buttons obtain the actor exclusively from Telegram from_user in the matching
private chat. «Ответить» enqueues `/reply` instructions; the service handles the
actual response. The transport neither sends Telegram messages directly nor
logs rejected input.

Outbox cards retain only task_id; the sender reads current content, state and
recipient permissions within its transaction. Stale buttons are checked again
by the service. Reminders use open_hour/close_hour, quiet hours, and
overdue_ping_minutes; last_ping_at/outbox changes are atomic, with one scheduler
marker per Moscow minute. Retention runs once per Moscow date. Completed task
content/references and outbox reply copies are cleared after done_ttl_days;
undelivered expired effects are cancelled. Completed tasks are excluded from the
queue. Maintenance is wired into run_jobs. R1/R2/R5/R6 sources will be wired in
T-402/T-303/T-403, R3 in T-304. R4/binding retry is connected to T-301. No YCLIENTS calls
are introduced here.
## Клиентская привязка T-301 / граница с T-203

`Candidate(client_id: positive int, phone: SecretStr)` — внутренний DTO поиска
клиента, не схема ответа YCLIENTS. `ClientLookup.candidates(SecretStr)` возвращает
кандидатов только из доверенного адаптера; сейчас runtime использует
`UnavailableClientLookup`. Подключать внешний адаптер можно после T-103.
Ноль/несколько совпадений, ошибка или timeout оставляют пользователя без ID и
создают R4. Телефон не попадает в логи, kv, users, аудит или outbox.
Привязка преподавателей (staff/renter) остаётся T-401 и не повышает роль client.

`ClientService.start/choice/contact/retry` сериализованы `BEGIN IMMEDIATE`;
состояние и обработанные request ID хранятся в kv без телефона. Epoch кнопок —
message ID последнего /start; смена этапа делает прежние кнопки недействительными.
Принятый выбор новости нельзя изменить повторной кнопкой. source — первая
корректная метка src_… при создании пользователя, без последующей перезаписи.
`client.onboard` разрешает только действия клиента над собой, `client.retry` —
только admin/owner над клиентом; свежие права адресата проверяются при повторе.

R4 создаётся через общий `rule("R4", "client:<Telegram ID>", ...)` и
`create_task(db, draft, cfg, now)`: requester_id=Telegram ID клиента,
ref=строка ID, dedupe_key=task_key("R4", "client:<Telegram ID>"), title из ru.py.
Создание задачи, kv identity (без телефона), last_ping_at, изменения onboarding
и уведомления `task.card` происходят в одной транзакции. Уведомления хранят
только task_id; текст и права перечитываются при отправке. Это тот же путь,
что и остальные задачи T-203, включая напоминания и TTL tombstones.

Телефон хранится только в body **открытой** R4 (ADR-06). R4 не переходит в taken:
«Взять» отклоняется с карточкой, направляющей к «Повторить привязку»; обычное
«Готово» тоже отклоняется. Карточка открытой R4 вместо Take/Done показывает
`cl:retry:<task_id>`, если свежие права администратора и клиента позволяют retry.
Закрытая R4 не показывает телефон или retry. Команды и старые кнопки не обходят
проверки сервиса. Это предусмотренный Issue #14 поток, ADR не изменены.

`ClientService.retry` принимает только open R4, перечитывает права обоих
пользователей и согласие клиента в BEGIN IMMEDIATE. При успехе сохраняет
проверенный ID, стирает body всех R4 клиента, закрывает задачи и уведомляет
клиента одной транзакцией. Ошибка сохраняет open R4 для следующего retry.
Конкурентные retry проверяют состояние после получения блокировки: только
одна успешная привязка и одно уведомление. Нельзя закрыть R4 без привязки или
вручную выдать YCLIENTS ID. Пока источник недоступен до T-103, live-привязка
остаётся отключённой; тесты используют исключительно синтетический lookup.

Outbox payload_min.reply_markup — JSON-строка Telegram-клавиатуры (сохраняется
скалярный контракт OutboxDraft). Sender валидирует клавиатуру при отправке;
request_contact и inline-кнопки отправляются только через outbox. Тихие часы
и границы гарантии доставки T-105 сохраняются.
