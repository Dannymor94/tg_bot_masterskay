# Бот студии «Мастерская»

Telegram-бот поверх YCLIENTS для администраторов, клиентов, преподавателей и владельца. В YCLIENTS бот выполняет **только чтение**; запись и отмену выполняет администратор в YCLIENTS.

**Состояние:** реализован фундамент T-101: конфигурация, SQLite, миграции, heartbeat и проверки. Пользовательские функции бота ещё не реализованы. Runner, GPT-ревью и диспетчер не входят в этот репозиторий: оркестрация агентов будет общей внешней системой (ADR-23). Пока её нет, Claude Code запускается человеком вручную.

## Источники истины

- [PLAN.md](PLAN.md) — задачи, зависимости и фактический статус.
- [docs/DECISIONS.md](docs/DECISIONS.md) — действующие ADR.
- [CONTEXT.md](CONTEXT.md) — факты о студии и YCLIENTS.
- [docs/API_MATRIX.yaml](docs/API_MATRIX.yaml) — допустимые операции API; до T-103 пути не подтверждены.
- [app/config/studio.yaml](app/config/studio.yaml) — параметры бизнес-правил.
- [AGENTS.md](AGENTS.md) и [CLAUDE.md](CLAUDE.md) — правила исполнителей.
- [docs/REVIEW.md](docs/REVIEW.md) — критерии проверки.
- [docs/COORDINATOR.md](docs/COORDINATOR.md) — роль координатора.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/CONTRACTS.md](docs/CONTRACTS.md), [docs/WORK_PACKAGES.md](docs/WORK_PACKAGES.md) — архитектура, договорённости модулей и параллельная работа.
- [docs/AUTONOMY.md](docs/AUTONOMY.md) — граница проекта и внешней системы агентов.

## Запуск разработки сейчас (внешнего диспетчера пока нет)

```bash
git clone https://github.com/Dannymor94/tg_bot_masterskay.git
cd tg_bot_masterskay
claude
```

В Claude Code: «Выполни T-101 строго по AGENTS.md и PLAN.md. Не выполняй push, merge или deploy». Исполнитель создаёт ветку, тесты, код, коммит и отчёт. Человек передаёт результат координатору на ревью. Команды разработки доступны после T-101; следующая задача — по PLAN после проверки координатором.

## Локальный запуск (Python 3.12 и uv)

```bash
make setup
make check
make migrate
make run
# В другом терминале:
make health
```

`make check`, миграции и health не требуют секретов. `make run` загружает переменные окружения и файл по ADR-07; окружение имеет приоритет. При отсутствии обязательной переменной процесс завершается с кодом 1 и сообщением «нет переменной X». Пустой `TELEGRAM_PROXY_URL` допускается.

Приложение применяет миграции перед первым heartbeat, пишет UTC в `data/bot.db` (WAL), завершается по SIGINT/SIGTERM. Параметры heartbeat находятся в `studio.yaml → runtime`. Health проверяет возраст heartbeat без создания отсутствующей БД. До следующих задач это проверка жизнеспособности процесса, а не доступности Telegram/YCLIENTS.

На VPS `docker compose up --build -d` запускает один сервис с bind volume `./data:/app/data` и секретами из внешнего файла ADR-07. Входящие порты отсутствуют. Развёртывание и проверка восстановления остаются T-204 и выполняются владельцем.

## Подготовка владельца (T-001)

Ограничить права приложения YCLIENTS до просмотра, подключить филиал, создать Telegram-бота, указать ссылку онлайн-записи в `studio.yaml`. Секреты размещать **только** в `~/.config/studio-bot/secrets.env` (`chmod 600`) по шаблону `config/env.example`. Никогда не размещать токены в GitHub, чате, screenshots или fixtures. Ранее раскрытые токены следует перевыпустить.

## Границы

Сначала локальная разработка и независимые тесты; VPS Германия и пилот — после приёмки этапов. Никаких автоматических merge/deploy. Никаких обращений к реальному API агентами без одобрения владельца.
