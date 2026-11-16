"""Role-constrained native Telegram Rich Messages; no unverified YCLIENTS data.

The renderer is deliberately pure apart from constructing references to packaged
artwork. Callback payloads are never trusted for authorization.
"""

from pathlib import Path
from typing import Any

from aiogram.types import FSInputFile, InputMediaPhoto, InputRichMessage, InputRichMessageMedia

from app.domain.models import User
from app.services.rich_views import screen_parts
from app.ui.rich_calendar import calendar_view
from app.ui.rich_detail import (
    bookings_view,
    broadcasts_view,
    questions_view,
    report_detail_view,
    report_view,
    tasks_view,
    team_view,
)

ART = Path(__file__).resolve().parent / "assets"

# Labels and menu structure are constants. A role is always loaded from the
# database by the service and revalidated before the outbox sends anything.
NAV: dict[str, tuple[tuple[str, str], ...]] = {
    "client": (
        ("home", "Главная"),
        ("schedule", "Расписание"),
        ("bookings", "Мои записи"),
        ("question", "Помощь"),
    ),
    "teacher": (
        ("home", "Главная"),
        ("lessons", "Мои занятия"),
        ("rent", "Аренда"),
        ("question", "Помощь"),
    ),
    "admin": (
        ("home", "Главная"),
        ("tasks", "Задачи"),
        ("broadcasts", "Рассылки"),
        ("team", "Доступ"),
    ),
    "owner": (
        ("home", "Главная"),
        ("tasks", "Задачи"),
        ("broadcasts", "Рассылки"),
        ("team", "Команда"),
        ("reports", "Отчёты"),
    ),
}

ROLE_TITLES = {
    "client": "Клиент",
    "teacher": "Преподаватель",
    "admin": "Администратор",
    "owner": "Владелец",
}


def view_role(actor: User) -> str:
    return "owner" if actor.is_owner else actor.role


def can_open(actor: User, screen: str) -> bool:
    try:
        base, mode, _ = screen_parts(screen)
    except ValueError:
        return False
    if base not in dict(NAV[view_role(actor)]):
        return False
    if mode == "detail" and base == "team" and not actor.is_owner:
        return False
    return True


def _button(label: str, target: str, *, active: bool = False) -> str:
    style = ' style="primary"' if active else ""
    return f'<tg-button type="callback_data"{style} data="ui:{target}">{label}</tg-button>'


def _row(values: list[str]) -> str:
    return f"<tg-button-row>{''.join(values)}</tg-button-row>"


def _body(actor: User, screen: str, data: dict[str, Any]) -> str:
    role = view_role(actor)
    base, _, _ = screen_parts(screen)
    if base == "tasks" and "tasks" in data:
        return tasks_view(data)
    if base == "broadcasts" and ("broadcasts" in data or "broadcast" in data):
        return broadcasts_view(data)
    if base == "team" and ("members" in data or "member" in data):
        return team_view(data, actor)
    if base == "reports" and "counts" in data:
        return report_view(data, owner=True)
    if base == "reports" and "report" in data:
        return report_detail_view(data)
    if base == "question" and "questions" in data:
        return questions_view(data)
    if base == "bookings" and "booking_requests" in data:
        return bookings_view(data)
    if base in ("schedule", "rent") and "calendar" in data:
        return calendar_view(data, rental=base == "rent")
    if base == "home" and role in ("admin", "owner") and "counts" in data:
        return report_view(data, owner=actor.is_owner)
    if screen == "home":
        if role == "client":
            return (
                "<h3>Добро пожаловать!</h3>"
                "<p>Посмотрите расписание, свои записи или задайте вопрос администратору.</p>"
                "<p>Информация о занятиях пока ожидает подтверждённого чтения из YCLIENTS.</p>"
                + _row(
                    [
                        _button(
                            "Отключить новости" if actor.news_opt_in else "Подписаться на новости",
                            "unsubscribe" if actor.news_opt_in else "subscribe",
                        )
                    ]
                )
            )
        if role == "teacher":
            return (
                "<h3>Ваш рабочий кабинет</h3>"
                "<p>Свои занятия, аренда залов и связь с администратором.</p>"
                "<p>Данные расписания появятся после проверки интеграции.</p>"
                + _row(
                    [
                        _button(
                            "Отключить новости" if actor.news_opt_in else "Подписаться на новости",
                            "unsubscribe" if actor.news_opt_in else "subscribe",
                        )
                    ]
                )
            )
        return (
            "<h3>Управление студией</h3>"
            "<p>Очередь задач, обращения, команда и рассылки доступны через меню.</p>"
            "<p>Отчёты по занятости пока ожидают подтверждения API YCLIENTS.</p>"
        )
    if screen == "schedule":
        return (
            "<h3>Расписание занятий</h3>"
            "<p>Данные ещё не подтверждены: вымышленные занятия и места не отображаются.</p>"
            "<p>До завершения T-103 запись оформляется через администратора.</p>"
            + _row([_button("Запросить запись", "compose:booking", active=True)])
            + _row([_button("Задать вопрос", "question")])
        )
    if screen == "bookings":
        return (
            "<h3>Мои записи</h3>"
            "<p>Раздел появится после привязки клиента и подтверждения чтения records.list.</p>"
            "<p>Пока бот не обещает запись, отмену или наличие мест.</p>"
            + _row([_button("Оставить заявку администратору", "compose:booking", active=True)])
        )
    if screen == "lessons":
        return (
            "<h3>Мои занятия</h3>"
            "<p>Будут показаны только ваши занятия после сверки учётной записи с YCLIENTS.</p>"
        )
    if screen == "rent":
        return (
            "<h3>Аренда залов</h3>"
            "<p>Большой зал, малый зал и чайная. Стоимость зависит от дня и времени.</p>"
            "<p>Свободные окна пока не подтверждены. Для брони обратитесь к администратору.</p>"
            + _row([_button("Запросить аренду", "compose:rent", active=True)])
            + _row([_button("Написать администратору", "question")])
        )
    if screen == "question":
        return (
            "<h3>Связь с администратором</h3>"
            "<p>Напишите сообщение командой:</p>"
            "<pre>/ask Ваш вопрос</pre>"
            "<p>Бот создаст реальную задачу R3 и уведомит администратора.</p>"
        )
    if screen == "tasks":
        return (
            "<h3>Очередь задач</h3>"
            "<p>Открывайте задачи, берите их в работу и отмечайте выполнение.</p>"
            + _row([_button("Показать живую очередь", "queue", active=True)])
            + "<p>Создать задачу: <b>/task текст задачи</b></p>"
        )
    if screen == "broadcasts":
        return (
            "<h3>Рассылки</h3>"
            "<p>Реальная рассылка запускается только после предпросмотра и подтверждения.</p>"
            "<pre>/announce clients_all Текст анонса</pre>"
            "<p>Сегменты и подписки проверяет сервис рассылок.</p>"
        )
    if screen == "team":
        invite = "<pre>/invite admin</pre>" if role == "owner" else ""
        revoke = "<pre>/revoke ID</pre>" if role == "owner" else ""
        return (
            "<h3>Команда и приглашения</h3>"
            "<p>Каждая роль привязана к подтверждённому Telegram ID.</p>"
            + _row([_button("Пригласить преподавателя", "invite_teacher", active=True)])
            + (
                _row([_button("Пригласить администратора", "invite_admin")])
                if role == "owner"
                else ""
            )
            + "<p>Одноразовая ссылка придёт в этот чат.</p>"
            + invite
            + revoke
            + "<p>Приглашения и отзыв доступа обрабатываются существующим RBAC.</p>"
        )
    if screen == "reports":
        return (
            "<h3>Аналитика студии</h3>"
            "<p>Загрузка залов и пустующие окна будут доступны после T-103/T-104.</p>"
            "<p>Непроверенные бизнес-показатели не отображаются.</p>"
        )
    raise ValueError("unknown view")


def render(actor: User, screen: str, data: dict[str, Any] | None = None) -> InputRichMessage:
    if not can_open(actor, screen):
        raise ValueError("screen outside role")
    role = view_role(actor)
    top = [
        "<h2>✿ Центр практик «Мастерская»</h2>",
        f"<p><b>{ROLE_TITLES[role]}</b> · управление прямо в Telegram</p>",
        '<img src="tg://photo?id=studio"/>',
    ]
    navigation = NAV[role]
    base, _, _ = screen_parts(screen)
    top.append(_row([_button(label, key, active=key == base) for key, label in navigation]))
    top.append(_body(actor, screen, data or {}))
    top.append("<p><i>YCLIENTS: только подтверждённые операции чтения.</i></p>")
    return InputRichMessage(
        html="\n".join(top),
        media=[
            InputRichMessageMedia(
                id="studio", media=InputMediaPhoto(media=FSInputFile(ART / f"{role}-{base}.png"))
            )
        ],
    )
