from pathlib import Path

import pytest
from aiogram.types import FSInputFile

from app.domain.models import User
from app.ui.rich_menu import NAV, can_open, render


@pytest.mark.parametrize("role", ["client", "teacher", "admin", "owner"])
def test_render_distinct_role_navigation_and_packaged_image(role):
    actor = User(
        telegram_user_id=7, role="admin" if role == "owner" else role, is_owner=role == "owner"
    )
    for screen, label in NAV[role]:
        rich = render(actor, screen)
        assert rich.html is not None
        assert f'data="ui:{screen}"' in rich.html
        assert label in rich.html
        assert len(rich.media or []) == 1
        assert rich.media is not None
        media = rich.media[0].media.media
        assert isinstance(media, FSInputFile)
        assert Path(media.path).is_file()
        assert Path(media.path).name == f"{role}.png"
        for different, items in NAV.items():
            if different == role:
                continue
            for key, _ in items:
                if key not in dict(NAV[role]):
                    assert f'data="ui:{key}"' not in rich.html


def test_forbidden_screen_and_no_unverified_business_claims():
    actor = User(telegram_user_id=7, role="client")
    assert not can_open(actor, "tasks")
    with pytest.raises(ValueError):
        render(actor, "tasks")
    for screen in ("schedule", "bookings"):
        html = render(actor, screen).html
        assert html is not None
        assert "YCLIENTS" in html or "не подтверждены" in html
        assert "/book" not in html
