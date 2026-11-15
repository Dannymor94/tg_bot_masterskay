# DECISIONS — все решения проекта

Единственное место решений. Открытых вопросов нет. Изменение решения — новая запись «ADR-NN заменяет ADR-MM». Числа — в `app/config/studio.yaml`.

## Архитектура

**ADR-01 · YCLIENTS — источник истины; бот только читает.** Используются только операции с `kind: read` в API_MATRIX (это может быть и POST, если это поиск). Бронь, отмены, абонементы, явку, клиентов заводит администратор в YCLIENTS. Тестовый филиал не нужен.

**ADR-02 · Один бот на все роли.** Меню зависит от роли; права проверяются в сервисе для каждого действия.

**ADR-03 · Один процесс, SQLite.** `python -m app` запускает в одном asyncio-цикле: polling бота, синхронизацию, планировщик, отправку из outbox. Хранилище — SQLite (WAL) в `data/bot.db`; SQLAlchemy 2 + Alembic. Нет контейнера БД, пароля БД, Redis, веб-сервера. Атомарность «Взять задачу» — условный `UPDATE … WHERE state='open'`. Локально — `uv`, на VPS — Docker Compose с одним сервисом.

**ADR-04 · Только опрос YCLIENTS, без webhook.** Каждые 5 минут — окно записей `[−1 день; +14 дней]`; изменения вычисляет чистая функция `diff(снимок_до, снимок_после)`. Обязательное поле webhook URL заполняется адресом `https://bot.yoga-rostov-mst.ru/yclients` без DNS-записи. Если YCLIENTS не сохраняет такой адрес — Quick Tunnel к заглушке, отвечающей 200 и отбрасывающей тело, только на время сохранения.

**ADR-05 · Размещение — VPS в Германии.** Входящих портов, кроме SSH, нет. Риск по 152-ФЗ принят владельцем; согласие — при `/start` со ссылкой на политику сайта.

**ADR-06 · Клиентская база — только в YCLIENTS.** Бот хранит: `telegram_user_id`, `@username`, имя профиля Telegram, `yclients_client_id`/`yclients_staff_id`, роль, подписки, время, статусы. Имена и телефоны из YCLIENTS подставляются в сообщения в момент отправки и не сохраняются. Телефон из `request_contact` используется только для поиска (исключение — открытая задача R4). Тексты закрытых задач удаляются через 90 дней. В логах — только идентификаторы.

**ADR-07 · Секреты вне репозитория.** `${HOME}/.config/studio-bot/secrets.env` (chmod 600) локально и на VPS. В репозитории — `config/env.example`. Правила `deny` Claude Code — второй слой.

**ADR-08 · Время.** В БД UTC; логика и тексты — `Europe/Moscow`.

## Роли и привязка

**ADR-09 · Клиент.** `/start` → согласие → «Получать новости?» (Да/Нет) → «Поделиться номером» (`request_contact`). Принимается только свой контакт (`contact.user_id == from_user.id`). Поиск по номеру: один клиент — привязка; ноль или несколько — задача R4 с номером, пользователю «Администратор проверит и напишет». Администратор находит или заводит клиента в YCLIENTS и жмёт «Повторить привязку»; при успехе номер стирается из задачи.

**ADR-10 · Преподаватель.** Одноразовая ссылка-приглашение от администратора (24 часа) → «Поделиться номером». Совпал с сотрудником YCLIENTS — штатный; с клиентом, у которого аренда за 180 дней, — арендатор; иначе R4. Штатный видит по своим занятиям число записавшихся и имена, без телефонов. Арендатор — только свои брони.

**ADR-11 · Администратор.** Первый владелец — `OWNER_TELEGRAM_ID`. Остальные — по приглашению. Отзыв доступа — у владельца. Выдачи и отзывы — в аудит.

## Сценарии

**ADR-12 · Уведомления администраторам.** Мгновенно: новая запись, отмена, перенос — групповые занятия и аренда. В тихие часы (22:00–07:00) не отправляются, а попадают в утреннюю сводку в 07:00: занятость залов и заполненность групп на сегодня, открытые задачи.

**ADR-13 · Задачи администраторов.** Общая очередь, кнопки «Взять», «Готово», для R3 — «Ответить». Невзятая в рабочие часы задача напоминает о себе через 60 минут. `dedupe_key` у каждой.

| Код | Когда | Задача |
|---|---|---|
| R1 | Преподаватель нажал «Хочу это окно» | «Забронировать: зал, дата, время, ориентир цены» |
| R2 | Клиент нажал «Не смогу» | «Отменить запись в YCLIENTS или связаться» (срок — начало занятия) |
| R3 | Клиент или преподаватель написал вопрос | «Ответить» — ответ уходит пользователю через бота |
| R4 | Привязка не удалась | «Найти или завести клиента в YCLIENTS» + «Повторить привязку» |
| R5 | Оценка занятия ≤ 3 | «Связаться по отзыву» |
| R6 | Еженедельно, по каждому из топ-5 пустых окон | «Предложить окно кандидатам» со списком арендаторов |
| R8 | Администратор создал вручную (`/task`) | Свободный текст |

R7 и R9 — в бэклоге (PLAN.md, раздел 7).

**ADR-14 · Клиент.** Меню: «Расписание» (групповые занятия на 7 дней и ссылка на онлайн-запись), «Мои записи», «Вопрос администратору», «Настройки» (новости). Напоминание накануне в 18:00 с «Приду» / «Не смогу»; «Не смогу» запись не отменяет, а создаёт R2. Через 2 часа после занятия — «Как прошло? 1–5».

**ADR-15 · Преподаватель.** Меню: «Мои занятия / аренды» (14 дней), «Свободные окна» (7 дней, с ценой), «Вопрос администратору», «Настройки» (еженедельная подборка окон). Уведомления: запись и отмена на свои занятия, напоминание об аренде накануне в 18:00.

**ADR-16 · Пустые окна аренды.** Еженедельный расчёт читает из API записи за последние 8 недель (снимки для этого не используются). Сетка: 3 зала × 7 дней × часы 07–22. Слот занят, если есть запись на колонке зала или групповое событие с ресурсом зала. «Стабильно пустой» — занят не более 1 из 8 недель. Соседние слоты склеиваются в окна, сортировка — по упущенной выручке (часы × цена). В понедельник: владельцу — топ-5 окон и загрузка залов; администраторам — задачи R6 (кандидаты — арендаторы, бравшие этот зал или этот день и время за 180 дней); подписанным преподавателям — подборка окон. Чем занять окно, решает администратор.

**ADR-17 · Отчёт по аренде не строится** — он в YCLIENTS. Цены — только для сортировки окон и ориентира в R1.

**ADR-18 · Рассылки.** Создаёт администратор: тип → сегмент или занятие → текст (фото по желанию) → предпросмотр с числом получателей → «Отправить».

| Тип | Кому | Условие |
|---|---|---|
| Анонс | Сегмент: все клиенты · все преподаватели · арендаторы · все | Только `news_opt_in`; кнопка «Отписаться» в каждом |
| Служебное | Привязанные участники выбранного занятия | Без подписки |

Получателей вычисляет чистая функция `segment()`. Отправка через outbox, `dedupe_key = broadcast_id:user_id`; повторная отправка невозможна. Автору — итог: доставлено / не доставлено / заблокировали / отписались. Для роста базы — ссылка `t.me/<bot>?start=src_<метка>` и QR в студии; метка сохраняется в `users.source`.

**ADR-19 · Штатные уведомления YCLIENTS не трогаем.** Дублирование с SMS на время пилота допустимо.

## Процесс

**ADR-20 · Разработка.** Исполнитель — Claude Code (Sonnet 4.6). Отдельное ревью — только для задач с меткой `[R]` (безопасность, данные, деньги-подобная логика, синхронизация); остальные проверяет `make check` и одно ревью в конце этапа. Merge и deploy — человек. Подробно — AGENTS.md.

**ADR-21 · Пилот.** Сначала неделя с администраторами (после этапа 2), затем 4 недели с клиентами и преподавателями; цели — `pilot_targets` в studio.yaml.

---

## Изменение процесса разработки

**ADR-22 · Заменяет ADR-20 только в части исполнителей и организации задач · утверждено владельцем 2026-11-12.** Целевой режим — максимально автономная параллельная разработка с независимым ревью, но merge, deploy, реальный `make spike`, секреты и изменение ADR остаются за владельцем. Claude Code (Sonnet 4.6) — основной исполнитель; ChatGPT может подготавливать код и документацию по поручению владельца; результаты всё равно проходят независимую проверку. Пока Runner и диспетчер не реализованы и не приняты, Claude Code запускается человеком вручную. Отдельные агенты — отдельные ветки/worktree; запрещена параллельная запись в одно дерево. Очерёдность реализации инфраструктуры: T-106 Runner, T-107 GPT-ревью, T-108 диспетчер. Отсутствие этих компонентов не блокирует T-101. Прочие решения ADR-20 (ревью `[R]`, ревью этапа и запрет автоматического merge/deploy) сохранены.

## Журнал токенов и прав (без значений)

| Дата | Событие | Кто |
|---|---|---|
| 2026-11-09 | Токен партнёра не перевыпускается — решение владельца, риск принят | Владелец |
| | User Token: перевыпущен / оставлен (решение владельца) | |
| | Права приложения сокращены до просмотра | |
| | Telegram-бот создан, токен в secrets.env | |

**ADR-23 · Заменяет часть ADR-22 о расположении инфраструктуры автоматизации · утверждено владельцем 2026-11-12.** Runner, GPT-ревьюер и диспетчер являются универсальной инфраструктурой управления агентами и **не входят** в репозиторий и план разработки Telegram-бота «Мастерская». Задачи T-106, T-107, T-108 исключаются из `PLAN.md` без отметки «выполнено» и не заменяются функциональностью бота. Цель автономной разработки сохраняется через будущую **внешнюю** систему. Пока её нет, Claude Code запускается вручную; ChatGPT может подготовить кодовые заготовки в `scaffolds/`, а агенты интегрируют их только по отдельным задачам и после независимой проверки. ADR-01–ADR-21 по продукту, а также правила ревью и ручного merge/deploy не меняются.

**ADR-24 · Supersedes ADR-20, ADR-22 and ADR-23 only for approval of development operations, integration and merge · owner-approved 2026-11-14.** The owner delegates standing authority to the ChatGPT coordinator/designated integrator to create/modify branches, PRs, Issues and task documents and to **merge eligible PRs automatically** with no repeated owner request. Required gates: current exact head/base and complete clean diff, green CI and `make check`, independent `[R]` or security/privacy/API review when required, no unresolved BLOCKER/MAJOR, no branch-protection bypass, and an exact-head guarded GitHub PR merge. A changed base/head invalidates stale approval until its diff and required independent review are repeated. Executors may push only their own task branches; reviewers do not modify reviewed code; integrator alone merges reviewed PRs. Post-merge status reconciliation and next eligible task are automatic. No 24/7 autonomous agent is implied until the external Runner/dispatcher exists (ADR-23). **Initial adoption of this process change is expressly authorized by the owner; it does not alter product/API security contracts.**

Routine development, integration, retargeting, nonproduction testing, QA handoffs and merges require **no further owner approval**. External authority remains with the owner for new real YCLIENTS probes, permissions and credentials, real customer/invite actions outside an already scoped authorization, materially new product/security ADRs or expanded MVP, payments/legal consent and initial production deployment without a pre-approved recoverable release pipeline. A documented standing authorization can cover repeated operations within its exact external scope without repeated prompts. Authorization to merge is not authorization to run live YCLIENTS, send Telegram messages or deploy to production. See `AGENTS.md`, `docs/COORDINATOR.md`, `docs/REVIEW.md` for deterministic gates. Other ADR-01–ADR-23 product decisions remain in force.

**ADR-25 · Supersedes ADR-20, ADR-22, ADR-23 and ADR-24 only for independent QA scheduling and review reuse · owner-approved 2026-11-15.** Development is **risk-based** as defined in `docs/QA_POLICY.md`: mandatory final-candidate GitHub CI and coordinator full effective diff review for every PR; **LOW** (docs, additive harmless synthetic tests) and **MEDIUM** (bounded non-sensitive app logic) integrate autonomously **without a separate QA agent by default**. **HIGH** (YCLIENTS/API/parser/probe, auth/permissions, secrets/PII, client recipients and actual sends, money-like effects, sync/outbox races, migration/data integrity, CI/deny/security controls or QA-governance reduction) requires one independent read-only **focused risk-surface** review. A historical `[R]` marker, new SHA, retarget or main advancing **alone** never triggers another independent reviewer: analyze the effective patch, dependencies, preserved evidence and CI; re-review only materially changed HIGH boundaries or unresolved BLOCKER/MAJOR. A successful final-head CI may be reused as verification of the complete test suite; independent reviewer performs targeted negative checks where valuable, not a duplicate full-suite default. At stage boundaries, consolidate only material cross-PR interaction risk into a single targeted review. The coordinator automatically merges eligible PRs with exact-head guard after required evidence, no repeated owner prompt. **Initial policy adoption itself is HIGH and must receive independent focused governance/security review under ADR-24 before merge.** All previous product, secret, live YCLIENTS, real Telegram, permission, deployment, GitHub branch-protection and owner authority gates remain unchanged. No unattended runner is implied.

**ADR-26 · Narrow pre-approval of routine task-branch push in Claude Code · owner-approved 2026-11-15.** The owner explicitly authorizes maximum autonomy for ordinary development publishing. Shared `.claude/settings.json` adds only `permissions.allow: ["Bash(git push -u origin HEAD)"]` for the exact command executed from the agent's verified dedicated task worktree. The project had **no existing `Bash(git push*)` deny** on main; this addresses ordinary prompts, not an actual platform/managed denial. Agents must still check worktree root, branch (never `main`), staged scope, exact commit and origin, and must not force-push, push to main, disable CI, override branch protection, inspect secrets or bypass managed/local permission rules. If a higher-priority rule or external sandbox still blocks the command, stop and report the specific blocker; modifying repo docs cannot grant the missing runtime permission. This exception does not authorize real YCLIENTS, Telegram sends, deploys or owner-only changes. Since this touches shared permission settings, initial adoption is **HIGH** under ADR-25 and requires one focused independent security/policy QA before merge. Future ordinary task-branch pushes do not require a new QA agent merely because they publish commits.
