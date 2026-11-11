# Бот студии «Мастерская»

Telegram-бот поверх YCLIENTS для администраторов, клиентов, преподавателей и владельца. В YCLIENTS бот выполняет **только чтение**; запись и отмену выполняет администратор в YCLIENTS.

**Состояние:** документация и план. Рабочий бот, Runner и диспетчер пока **не реализованы**. Автономный процесс — цель разработки, а не уже существующий сервис.

## Источники истины

- [PLAN.md](PLAN.md) — задачи, зависимости и фактический статус.
- [docs/DECISIONS.md](docs/DECISIONS.md) — действующие ADR.
- [CONTEXT.md](CONTEXT.md) — факты о студии и YCLIENTS.
- [docs/API_MATRIX.yaml](docs/API_MATRIX.yaml) — допустимые операции API; до T-103 пути не подтверждены.
- [app/config/studio.yaml](app/config/studio.yaml) — параметры бизнес-правил.
- [AGENTS.md](AGENTS.md) и [CLAUDE.md](CLAUDE.md) — правила исполнителей.
- [docs/REVIEW.md](docs/REVIEW.md) — критерии проверки.
- [docs/COORDINATOR.md](docs/COORDINATOR.md) — роль координатора.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/CONTRACTS.md](docs/CONTRACTS.md), [docs/AUTONOMY.md](docs/AUTONOMY.md) — границы модулей и путь к автоматизации.

## Запуск разработки сейчас (без диспетчера)

```bash
git clone https://github.com/Dannymor94/tg_bot_masterskay.git
cd tg_bot_masterskay
claude
```

В Claude Code: «Выполни T-101 строго по AGENTS.md и PLAN.md. Не выполняй push, merge или deploy». Исполнитель создаёт ветку, тесты, код, коммит и отчёт. Человек передаёт результат координатору на ревью. **Команды make ещё не существуют** — их создаёт T-101.

## Подготовка владельца (T-001)

Ограничить права приложения YCLIENTS до просмотра, подключить филиал, создать Telegram-бота, указать ссылку онлайн-записи в `studio.yaml`. Секреты размещать **только** в `~/.config/studio-bot/secrets.env` (`chmod 600`) по шаблону `config/env.example`. Никогда не размещать токены в GitHub, чате, screenshots или fixtures. Ранее раскрытые токены следует перевыпустить.

## Границы

Сначала локальная разработка и независимые тесты; VPS Германия и пилот — после приёмки этапов. Никаких автоматических merge/deploy. Никаких обращений к реальному API агентами без одобрения владельца.
