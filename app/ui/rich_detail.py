"""Escape and render transient, permission-filtered Rich Message view models."""

from html import escape
from typing import Any

from app.domain.models import User

TASK_STATUS = {"open": "Ожидает", "taken": "В работе", "done": "Готово"}
FILTER_NAMES = {"active": "Текущие", "open": "Новые", "taken": "Взятые", "done": "Закрытые"}
ROLE_NAMES = {"admin": "Администратор", "teacher": "Преподаватель"}
NEWS_STATE = {
    "draft": "Черновик",
    "sending": "Отправляется",
    "done": "Готово",
    "cancelled": "Отменена",
}


def button(label: str, callback: str, *, primary: bool = False) -> str:
    style = ' style="primary"' if primary else ""
    # Callback payloads are assembled from fixed tokens and validated integers.
    return (
        f'<tg-button type="callback_data"{style} '
        f'data="{escape(callback, quote=True)}">{escape(label)}</tg-button>'
    )


def row(*items: str) -> str:
    return "<tg-button-row>" + "".join(items) + "</tg-button-row>"


def copy_command(label: str, command: str) -> str:
    return row(
        f'<tg-button type="copy_text" text="{escape(command, quote=True)}">'
        f"{escape(label)}</tg-button>"
    )


def tasks_view(data: dict[str, Any]) -> str:
    state, page, pages = data["filter"], data["page"], data["pages"]
    lines = [f"<h3>Очередь · {FILTER_NAMES[state]} ({data['count']})</h3>"]
    lines.append(
        row(
            *(
                button(
                    label,
                    "ui:tasks" if key == "active" else f"ui:tasks:{key}:0",
                    primary=key == state,
                )
                for key, label in FILTER_NAMES.items()
            )
        )
    )
    if not data["tasks"]:
        lines.append("<p>Задач в этом разделе нет.</p>")
    for task in data["tasks"]:
        task_id = task["id"]
        lines.append(
            f"<p><b>#{task_id} · {escape(task['code'])} · "
            f"{TASK_STATUS.get(task['state'], '—')}</b>"
            f"\n{escape(task['title'])}"
            f"\n{escape(task['body'])}</p>"
        )
        if task["due"]:
            lines.append(f"<p>Срок: {escape(task['due'])} UTC</p>")
        actions = []
        context = f"{task_id}:{state}:{page}"
        if task["take"]:
            actions.append(button("Взять", f"ui:task:take:{context}", primary=True))
        if task["finish"]:
            actions.append(button("Готово", f"ui:task:done:{context}", primary=True))
            if task["reply"]:
                actions.append(button("Ответить", f"ui:task:reply:{context}"))
        if task["code"] == "R4":
            lines.append("<p>Ожидается повторная привязка со стороны клиента.</p>")
        if actions:
            lines.append(row(*actions))
        lines.append("<hr/>")
    page_actions = []
    if page > 0:
        page_actions.append(button("← Назад", f"ui:tasks:{state}:{page - 1}"))
    if page + 1 < pages:
        page_actions.append(button("Далее →", f"ui:tasks:{state}:{page + 1}"))
    if page_actions:
        lines.append(row(*page_actions))
    lines.append(f"<p>Страница {page + 1} из {pages}</p>")
    lines.append(row(button("＋ Создать задачу", "ui:compose:task", primary=True)))
    lines.append("<p>Бот попросит описание и добавит задачу в очередь.</p>")
    return "\n".join(lines)


def questions_view(data: dict[str, Any]) -> str:
    lines = ["<h3>Мои обращения</h3>", "<p>Здесь реальные вопросы, переданные администраторам.</p>"]
    if not data["questions"]:
        lines.append("<p>Вы пока не отправляли обращений.</p>")
    for task in data["questions"]:
        lines.append(f"<p>Вопрос #{task['id']} · {TASK_STATUS.get(task['state'], '—')}</p>")
    lines.append(row(button("＋ Задать вопрос", "ui:compose:question", primary=True)))
    lines.append("<p>Отправьте вопрос ответом на сообщение бота.</p>")
    return "\n".join(lines)


def bookings_view(data: dict[str, Any]) -> str:
    lines = [
        "<h3>Заявки на запись</h3>",
        "<p>Это запросы администратору, а не подтверждённые записи в YCLIENTS.</p>",
    ]
    if not data["booking_requests"]:
        lines.append("<p>Пока нет заявок. Данные реальных записей недоступны.</p>")
    for item in data["booking_requests"]:
        lines.append(
            f"<p>Заявка #{item['id']} · {TASK_STATUS.get(item['state'], '—')}"
            f"\n{escape(item['text'])}</p>"
        )
    lines.append(row(button("＋ Запросить запись", "ui:compose:booking", primary=True)))
    return "\n".join(lines)


def broadcasts_view(data: dict[str, Any]) -> str:
    if "broadcast" in data:
        b = data["broadcast"]
        if b is None:
            return "<h3>Рассылка не найдена</h3>" + row(button("К списку", "ui:broadcasts"))
        lines = [
            f"<h3>Рассылка #{b['id']} · {NEWS_STATE.get(b['state'], '—')}</h3>",
            f"<p>Получатели: {escape(b['segment'])}</p>",
            f"<p>{escape(b['text'])}</p>",
        ]
        if b["state"] == "draft":
            lines.append("<p>Внимание: отправка доставит сообщение выбранным подписчикам.</p>")
            lines.append(
                row(
                    button("Подтвердить отправку", f"ui:bc:confirm:{b['id']}", primary=True),
                    button("Отменить черновик", f"ui:bc:cancel:{b['id']}"),
                )
            )
        else:
            lines.append(f"<p>Доставлено: {b['sent']} · не доставлено: {b['failed']}</p>")
        lines.append(row(button("К рассылкам", "ui:broadcasts")))
        return "\n".join(lines)
    lines = ["<h3>Мои рассылки</h3>"]
    if not data["broadcasts"]:
        lines.append("<p>Рассылок пока нет.</p>")
    for b in data["broadcasts"]:
        lines.append(
            f"<p>#{b['id']} · {escape(b['segment'])} · {NEWS_STATE.get(b['state'], '—')}</p>"
        )
        lines.append(row(button("Подробнее", f"ui:broadcasts:detail:{b['id']}")))
    lines.append("<h4>Новая рассылка</h4>")
    lines.append(row(button("Клиентам", "ui:compose:announce:clients_all", primary=True)))
    lines.append(row(button("Преподавателям", "ui:compose:announce:teachers_all")))
    lines.append(row(button("Арендаторам", "ui:compose:announce:teachers_renters")))
    lines.append(row(button("Всем подписчикам", "ui:compose:announce:everyone")))
    lines.append("<p>После ввода текста появится черновик для отдельного подтверждения.</p>")
    return "\n".join(lines)


def team_view(data: dict[str, Any], actor: User) -> str:
    if "member" in data:
        member = data["member"]
        if member is None:
            return "<h3>Участник не найден</h3>" + row(button("Назад", "ui:team"))
        lines = [
            f"<h3>Доступ Telegram ID {member['id']}</h3>",
            f"<p>{ROLE_NAMES.get(member['role'], 'Участник')}</p>",
        ]
        if member["revoked"]:
            lines.append("<p>Доступ уже отозван.</p>")
        else:
            lines.append(
                "<p>Подтвердите отзыв доступа. Участник потеряет доступ к рабочим разделам.</p>"
            )
            lines.append(
                row(button("Отозвать доступ", f"ui:team:revoke:{member['id']}", primary=True))
            )
        lines.append(row(button("Назад к команде", "ui:team")))
        return "\n".join(lines)
    lines = [
        "<h3>Доступ и команда</h3>",
        row(button("Пригласить преподавателя", "ui:invite_teacher")),
    ]
    if actor.is_owner:
        lines.append(row(button("Пригласить администратора", "ui:invite_admin")))
        for m in data.get("members", []):
            marker = " · доступ отозван" if m["revoked"] else ""
            lines.append(f"<p>ID {m['id']} · {ROLE_NAMES.get(m['role'], '—')}{marker}</p>")
            if not m["revoked"]:
                lines.append(row(button("Управлять", f"ui:team:detail:{m['id']}")))
    lines.append("<p>Приглашение придёт в личный чат создателя ссылки.</p>")
    return "\n".join(lines)


def report_view(data: dict[str, Any], *, owner: bool = False) -> str:
    metrics = data.get("counts", {})
    lines = [
        "<h3>Оперативная сводка</h3>",
        f"<p>Ожидают: {metrics.get('open', 0)} · в работе: {metrics.get('taken', 0)} "
        f"· завершены: {metrics.get('done', 0)}</p>",
    ]
    if owner:
        lines.append(f"<p>Подписчиков новостей: {metrics.get('subscribers', 0)}</p>")
    lines.append(row(button("Открыть задачи", "ui:tasks")))
    lines.append(
        "<p>Это локальные показатели бота. Загрузка залов и доходы YCLIENTS "
        "пока не подтверждены.</p>"
    )
    return "\n".join(lines)


REPORT_TITLES = {
    "R1": "Запросы аренды",
    "R2": "Отмены посещений",
    "R3": "Вопросы и заявки",
    "R4": "Проверка привязки",
    "R5": "Низкая оценка",
    "R6": "Свободные окна (события)",
    "R8": "Ручные задачи",
}


def report_detail_view(data: dict[str, Any]) -> str:
    """Show what the local numbers mean and what the bot cannot yet know."""
    info = data["report"]
    mode = info["mode"]
    lines = [
        "<h3>Отчёт «Мастерской»</h3>",
        row(
            button("Обзор", "ui:reports", primary=mode == "summary"),
            button("Задачи", "ui:reports:tasks", primary=mode == "tasks"),
            button("Рассылки", "ui:reports:news", primary=mode == "news"),
            button("Люди", "ui:reports:people", primary=mode == "people"),
            button("Доставка", "ui:reports:operations", primary=mode == "operations"),
        ),
    ]
    if mode in ("summary", "tasks"):
        s = info["states"]
        lines.extend(
            (
                "<h4>Очередь задач</h4>",
                f"<p>Ожидают исполнителя: <b>{s['open']}</b></p>",
                f"В работе у команды: <b>{s['taken']}</b>",
                f"Выполнены за всё время: <b>{s['done']}</b>",
            )
        )
        if mode == "tasks":
            lines.append("<h4>По направлениям</h4>")
            for code, name in REPORT_TITLES.items():
                c = info["codes"].get(code, {})
                active = c.get("open", 0) + c.get("taken", 0)
                if active or c.get("done", 0):
                    lines.append(
                        f"<p>{name}: <b>{active} активных</b> · {c.get('done', 0)} закрыто</p>"
                    )
            lines.append(row(button("Открыть очередь", "ui:tasks")))
        else:
            lines.append("<p>Цифры взяты из локальной таблицы задач бота.</p>")
    if mode in ("summary", "news"):
        n = info["news"]
        lines.extend(
            (
                "<h4>Новости и рассылки</h4>",
                f"<p>Черновиков: <b>{n.get('draft', 0)}</b> · "
                f"в отправке: <b>{n.get('sending', 0)}</b> · "
                f"завершены: <b>{n.get('done', 0)}</b></p>",
                f"<p>Доставлено сообщений: <b>{info['delivered']}</b> · "
                f"ошибки доставки: <b>{info['failed']}</b></p>",
            )
        )
        if mode == "news":
            lines.append("<p>Счётчики накоплены только по рассылкам этого бота.</p>")
            lines.append(row(button("Открыть рассылки", "ui:broadcasts")))
    if mode in ("summary", "people"):
        r = info["roles"]
        lines.extend(
            (
                "<h4>Пользователи бота</h4>",
                f"<p>Администраторы: <b>{r.get('admin', 0)}</b> · "
                f"преподаватели: <b>{r.get('teacher', 0)}</b> · "
                f"клиенты: <b>{r.get('client', 0)}</b></p>",
                f"<p>Подписаны на новости: <b>{info['subscribers']}</b></p>",
            )
        )
        if mode == "people":
            lines.append("<p>Считаются активные Telegram ID, не посетители CRM.</p>")
            lines.append(row(button("Открыть команду", "ui:team")))
    if mode in ("summary", "operations"):
        d = info["delivery"]
        total_overdue = sum(info["overdue"].values())
        lines.extend(
            (
                "<h4>Оперативный контроль</h4>",
                f"<p>Просроченных задач: <b>{total_overdue}</b> · "
                f"сообщений в очереди: <b>{d['pending']}</b></p>",
                f"<p>Ошибки отправки: <b>{d['failed']}</b> · "
                f"заблокировано получателями: <b>{d['blocked']}</b></p>",
            )
        )
        if mode == "operations":
            lines.append("<h4>Просроченные по направлениям</h4>")
            if total_overdue:
                for code, name in REPORT_TITLES.items():
                    count = info["overdue"].get(code, 0)
                    if count:
                        lines.append(f"<p>{name}: <b>{count}</b></p>")
            else:
                lines.append("<p>Задач с истёкшим сроком нет.</p>")
            lines.extend(
                (
                    "<p>Просроченными считаются открытые и взятые задачи "
                    "с указанным сроком, который уже прошёл. Задачи без срока не учитываются.</p>",
                    "<h4>Очередь сообщений Telegram</h4>",
                    f"<p>Ожидают отправки: <b>{d['pending']}</b></p>",
                    f"<p>Приняты API Telegram: <b>{d['sent']}</b></p>",
                    f"<p>Ошибки: <b>{d['failed']}</b> · "
                    f"получатель недоступен: <b>{d['blocked']}</b> · "
                    f"отменены: <b>{d['cancelled']}</b> · "
                    f"отписка: <b>{d['unsubscribed']}</b></p>",
                    "<p>Считаются все сообщения бота, включая служебные и экраны меню. "
                    "Принято API не означает, что человек прочитал сообщение. "
                    "Счётчики накопительные, а не за сегодня.</p>",
                    row(button("Открыть очередь задач", "ui:tasks")),
                )
            )
    lines.append(
        "<p><i>Данные обновляются при открытии отчёта. Это реальные локальные "
        "показатели бота, не выручка, посещаемость и не загрузка залов YCLIENTS. "
        "CRM-отчёты недоступны до проверки API.</i></p>"
    )
    return "\n".join(lines)
