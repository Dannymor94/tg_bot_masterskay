import pytest

from app.runtime import migrate


@pytest.fixture
def task_db(tmp_path):
    path = tmp_path / "tasks.db"
    migrate(path)
    return path
