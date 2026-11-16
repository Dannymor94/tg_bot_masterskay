"""Produce original studio artwork for native Telegram Rich Message cards.

Pillow is a *generation-time* tool; the application loads committed PNG assets.
No customer or YCLIENTS data is involved.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TARGET = Path(__file__).resolve().parent.parent / "app" / "ui" / "assets"
CARDS = {
    "client": ("Практики студии", "Занятия · расписание · вопросы", "lotus"),
    "admin": ("Работа администратора", "Задачи · обращения · рассылки", "notes"),
    "teacher": ("Кабинет преподавателя", "Свои занятия · аренда · помощь", "lotus"),
    "owner": ("Управление Мастерской", "Команда · процессы · отчёты", "chart"),
}
SCREENS = {
    "client": {
        "home": ("Добро пожаловать", "Практики · забота · новости", "lotus"),
        "schedule": ("Календарь практик", "Выбор даты · запрос записи", "calendar"),
        "bookings": ("Мои заявки", "История запросов администратору", "ticket"),
        "question": ("На связи", "Ваши вопросы и ответы", "chat"),
    },
    "teacher": {
        "home": ("Кабинет преподавателя", "Практики · аренда · поддержка", "yoga"),
        "lessons": ("Мои занятия", "Расписание преподавателя", "book"),
        "rent": ("Аренда зала", "Выберите удобный день", "room"),
        "question": ("Связь с командой", "Вопросы администратору", "chat"),
    },
    "admin": {
        "home": ("Рабочий день", "Задачи · команда · события", "notes"),
        "tasks": ("Очередь задач", "Принимать · выполнять · отвечать", "checklist"),
        "broadcasts": ("Рассылки", "Анонсы и новости студии", "megaphone"),
        "team": ("Управление доступом", "Приглашения преподавателей", "team"),
    },
    "owner": {
        "home": ("Мастерская сегодня", "Общая картина процессов", "lotus"),
        "tasks": ("Работа команды", "Задачи · статусы · контроль", "checklist"),
        "broadcasts": ("Коммуникации", "Анонсы · подписчики", "megaphone"),
        "team": ("Моя команда", "Участники · доступы · роли", "team"),
        "reports": ("Отчёты студии", "Реальные данные · понятные цифры", "chart"),
    },
}


def face(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)


def illustration(draw: ImageDraw.ImageDraw, symbol: str) -> None:
    if symbol in {
        "calendar",
        "ticket",
        "chat",
        "yoga",
        "book",
        "room",
        "checklist",
        "megaphone",
        "team",
    }:
        draw.rounded_rectangle((63, 43, 215, 207), radius=22, fill="#dbe9ed")
        if symbol == "calendar":
            draw.rounded_rectangle(
                (81, 70, 199, 183), radius=12, fill="#f8fcfd", outline="#7395a7", width=3
            )
            draw.rounded_rectangle((81, 70, 199, 101), radius=8, fill="#f2af7e")
            for x in (110, 151, 183):
                for y in (119, 144, 168):
                    draw.ellipse(
                        (x - 5, y - 5, x + 5, y + 5),
                        fill="#4c9aa6" if x != 151 or y != 144 else "#ee9f72",
                    )
        elif symbol == "ticket":
            draw.rounded_rectangle((76, 99, 203, 156), radius=13, fill="#f2b17e")
            draw.line((139, 103, 139, 152), fill="#dbe9ed", width=3)
            draw.ellipse((92, 115, 114, 137), outline="#436d7e", width=4)
            draw.line((156, 127, 167, 139, 186, 114), fill="#386f7b", width=5)
        elif symbol == "chat":
            draw.rounded_rectangle((79, 78, 173, 139), radius=20, fill="#7eb1b7")
            draw.polygon(((109, 134), (96, 156), (102, 134)), fill="#7eb1b7")
            draw.rounded_rectangle((111, 122, 204, 171), radius=18, fill="#f2b17e")
            draw.polygon(((177, 167), (192, 184), (190, 162)), fill="#f2b17e")
            for x in (132, 152, 172):
                draw.ellipse((x, 143, x + 6, 149), fill="#527b89")
        elif symbol == "yoga":
            draw.ellipse((124, 64, 157, 97), fill="#f2b17e")
            draw.arc((104, 92, 180, 178), 185, 355, fill="#488c9c", width=9)
            draw.line((141, 112, 141, 160), fill="#488c9c", width=10)
            draw.arc((86, 141, 197, 202), 5, 172, fill="#488c9c", width=10)
            draw.line((94, 193, 191, 193), fill="#efb280", width=5)
        elif symbol == "book":
            draw.polygon(((81, 81), (133, 94), (133, 187), (81, 174)), fill="#8cb4bd")
            draw.polygon(((138, 94), (195, 81), (195, 174), (138, 187)), fill="#f2b17e")
            draw.line((136, 89, 136, 188), fill="#507d8a", width=5)
            draw.line((92, 114, 118, 121), fill="#dbe9ed", width=4)
            draw.line((151, 119, 183, 111), fill="#dbe9ed", width=4)
        elif symbol == "room":
            draw.rounded_rectangle((93, 64, 188, 195), radius=7, fill="#78aebb")
            draw.rectangle((108, 83, 175, 195), fill="#f2b17e")
            draw.ellipse((157, 136, 166, 146), fill="#52798a")
            draw.line((110, 195, 194, 195), fill="#4b8599", width=7)
        elif symbol == "checklist":
            draw.rounded_rectangle((83, 65, 195, 191), radius=10, fill="#f8fcfd")
            for y in (94, 132, 170):
                draw.rounded_rectangle(
                    (98, y - 9, 115, y + 8), radius=3, outline="#65969f", width=3
                )
                draw.line((99, y, 107, y + 6, 120, y - 9), fill="#eea572", width=4)
                draw.line((129, y, 178, y), fill="#7a9fa8", width=5)
        elif symbol == "megaphone":
            draw.polygon(((85, 112), (154, 76), (154, 162), (85, 146)), fill="#f2b17e")
            draw.rounded_rectangle((76, 112, 104, 149), radius=7, fill="#6ca9b7")
            draw.polygon(((104, 147), (122, 184), (143, 179), (128, 140)), fill="#4d8191")
            draw.arc((145, 75, 206, 158), 275, 82, fill="#69a3b6", width=6)
        elif symbol == "team":
            for x, y, color in ((104, 103, "#f2b17e"), (168, 103, "#80b3bb"), (137, 79, "#e5c19f")):
                draw.ellipse((x - 18, y - 18, x + 18, y + 18), fill=color)
            draw.rounded_rectangle((80, 138, 201, 181), radius=22, fill="#5f9fae")
            draw.rounded_rectangle((105, 125, 169, 182), radius=23, fill="#f2b17e")
        return
    if symbol == "lotus":
        draw.ellipse((101, 59, 157, 115), fill="#f4c49c")
        draw.rounded_rectangle((100, 114, 158, 190), radius=22, fill="#a5ccc4")
        draw.polygon(
            ((101, 147), (55, 195), (115, 186), (131, 172), (146, 186), (201, 195), (156, 147)),
            fill="#8baeb2",
        )
        draw.rounded_rectangle((58, 196, 200, 207), radius=6, fill="#f3ad7b")
        draw.arc((71, 47, 186, 185), 184, 357, fill="#9ed7e2", width=3)
    elif symbol == "notes":
        draw.rounded_rectangle((71, 44, 206, 209), radius=18, fill="#e2eaf0")
        draw.rounded_rectangle((109, 32, 169, 58), radius=12, fill="#f2b17e")
        for y in (87, 118, 149, 180):
            draw.rounded_rectangle((92, y, 113, y + 20), radius=6, fill="#f2b17e")
            draw.line((125, y + 10, 186, y + 10), fill="#638397", width=5)
    else:
        draw.rounded_rectangle((63, 43, 215, 207), radius=22, fill="#d9e8ed")
        for i, h in enumerate((55, 85, 112)):
            x = 86 + i * 41
            draw.rounded_rectangle(
                (x, 180 - h, x + 23, 180), radius=7, fill=("#69abb7", "#498a9d", "#f2b17e")[i]
            )
        draw.line((85, 185, 197, 185), fill="#6b92a5", width=5)


def create(title: str, subtitle: str, symbol: str) -> Image.Image:
    image = Image.new("RGB", (760, 250), "#184260")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (12, 12, 748, 238), radius=25, fill="#405b70", outline="#66879a", width=2
    )
    illustration(draw, symbol)
    draw.text((263, 65), title, fill="#f2fafb", font=face(31, True))
    draw.text((265, 116), subtitle, fill="#badbe7", font=face(19))
    draw.rounded_rectangle((263, 158, 505, 203), radius=19, fill="#205b7d")
    draw.text((286, 168), "МАСТЕРСКАЯ", fill="#d8edf4", font=face(17, True))
    return image


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for role, (title, subtitle, symbol) in CARDS.items():
        create(title, subtitle, symbol).save(TARGET / f"{role}.png", optimize=True)
    for role, screens in SCREENS.items():
        for screen, (title, subtitle, symbol) in screens.items():
            create(title, subtitle, symbol).save(TARGET / f"{role}-{screen}.png", optimize=True)


if __name__ == "__main__":
    main()
