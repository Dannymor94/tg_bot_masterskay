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


def face(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)


def illustration(draw: ImageDraw.ImageDraw, symbol: str) -> None:
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


if __name__ == "__main__":
    main()
