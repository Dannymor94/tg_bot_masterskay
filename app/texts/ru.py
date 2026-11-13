MISSING_VARIABLE = "нет переменной {name}"
INVALID_SETTINGS = "Некорректные настройки: {fields}"
INVALID_STUDIO = "Некорректный studio.yaml"
HEALTH_OK = "Heartbeat актуален"
HEALTH_FAILED = "Heartbeat отсутствует или устарел"
ACCESS_DENIED = "Доступ запрещён"
ROLE_MENU = {
    "client": "Доступ клиента подтверждён.",
    "teacher": "Доступ преподавателя подтверждён.",
    "admin": (
        "Доступ администратора подтверждён. /invite teacher; владелец: /invite admin, /revoke ID."
    ),
}

INVITE_ACCEPTED = {
    "admin": "Приглашение принято. Доступ администратора выдан.",
    "teacher": "Приглашение принято. Для доступа преподавателя потребуется подтверждение привязки.",
}

INVITE_LINK = "Одноразовое приглашение: https://t.me/{username}?start=invite_{token}"

ACCESS_REVOKED = "Доступ пользователя {user_id} отозван."

# T-102: safe YCLIENTS adapter errors and diagnostic redaction.
YCLIENTS_ERROR = "Ошибка адаптера YCLIENTS"
YCLIENTS_DENIED = "Операция YCLIENTS запрещена"
YCLIENTS_UNAVAILABLE = "Операция YCLIENTS недоступна; примените fallback"
REDACTED = "[REDACTED]"
TASK_TITLES = {
    "R1": "Забронировать: зал, дата, время, ориентир цены",
    "R2": "Отменить запись в YCLIENTS или связаться",
    "R3": "Ответить на вопрос",
    "R4": "Найти или завести клиента в YCLIENTS",
    "R5": "Связаться по отзыву",
    "R6": "Предложить окно кандидатам",
    "R8": "Задача администратора",
}
TASK_CARD = "Задача #{id} · {code} · {state}\n{title}\n{body}\nСрок: {due}"
TASK_TAKE = "Взять"
TASK_DONE = "Готово"
TASK_REPLY = "Ответить"
TASK_REPLY_HELP = "Для ответа: /reply {id} текст ответа"
TASK_REPLY_TEXT = "Ответ администратора:\n{text}"
TASK_CHANGED = "Задача #{id}: {state}"
