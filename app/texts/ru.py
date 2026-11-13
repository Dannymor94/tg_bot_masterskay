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

CLIENT_CONSENT = "Для привязки подтвердите согласие с политикой: {url}"
CLIENT_AGREE = "Согласен"
CLIENT_NEWS = "Получать новости?"
CLIENT_YES = "Да"
CLIENT_NO = "Нет"
CLIENT_CONTACT = "Поделитесь своим номером кнопкой ниже."
CLIENT_SHARE = "Поделиться номером"
CLIENT_WAIT = "Администратор проверит и напишет."
CLIENT_LINKED = "Привязка выполнена."
CLIENT_R4_TITLE = "Найти или завести клиента в YCLIENTS"
CLIENT_R4_NOTICE = "Нужна проверка привязки: задача R4 №{task_id}."
CLIENT_RETRY = "Повторить привязку"
CLIENT_RETRY_FAILED = "Привязка пока не выполнена. Проверьте клиента в YCLIENTS и повторите."
CLIENT_RETRY_OK = "Привязка выполнена; номер удалён из задачи."
