"""Native Telegram seven-column date grid, not an unverified availability grid."""

from datetime import date
from html import escape
from typing import Any

from app.ui.rich_detail import button, row


def calendar_view(data: dict[str, Any], *, rental: bool) -> str:
    model = data["calendar"]
    screen = "rent" if rental else "schedule"
    action = "rent" if rental else "booking"
    lines = [
        "<h3>Выбор даты аренды</h3>" if rental else "<h3>Календарь занятий</h3>",
        f"<h4>{escape(model['title'])}</h4>",
        "<p><b>Доступность:</b> пока не подтверждена YCLIENTS. "
        "Дата — это пожелание, не свободный слот и не бронь.</p>",
    ]
    if model.get("notice"):
        lines.append(f"<p>{escape(model['notice'])}</p>")
    lines.append("<p>Пн　 Вт　 Ср　 Чт　 Пт　 Сб　 Вс</p>")
    for week in model["rows"]:
        cells = []
        for cell in week:
            if not cell["enabled"]:
                cells.append(f'<tg-button type="disabled">{cell["label"]}</tg-button>')
            else:
                cells.append(
                    button(
                        cell["label"],
                        f"ui:{screen}:day:{cell['date']}",
                        primary=cell["date"] == model["selected"],
                    )
                )
        lines.append(row(*cells))
    page = []
    if model["previous"]:
        page.append(button("← Месяц", f"ui:{screen}:{model['previous']}"))
    if model["next"]:
        page.append(button("Месяц →", f"ui:{screen}:{model['next']}"))
    if page:
        lines.append(row(*page))
    if model["selected"]:
        selected = date.fromisoformat(model["selected"])
        lines.append(f"<p>Выбрано: <b>{selected.strftime('%d.%m.%Y')}</b>.</p>")
        lines.append(
            row(
                button(
                    "Уточнить аренду у администратора"
                    if rental
                    else "Запросить запись на эту дату",
                    f"ui:compose:{action}:{model['selected']}",
                    primary=True,
                )
            )
        )
        lines.append("<p>Бот попросит желаемое время и название занятия/зала.</p>")
    else:
        lines.append("<p>Выберите день в календаре, чтобы отправить запрос администратору.</p>")
        lines.append(row(button("Написать без выбора даты", f"ui:compose:{action}")))
    return "\n".join(lines)
