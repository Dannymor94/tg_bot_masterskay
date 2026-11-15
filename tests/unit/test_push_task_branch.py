"""Offline regression tests for the narrowly allowed task-branch push."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "push_task_branch.sh"
REAL_GIT = shutil.which("git")


def _git(repo: Path, *args: str) -> None:
    assert REAL_GIT is not None
    subprocess.run(
        [REAL_GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def task_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    assert REAL_GIT is not None
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "remote", "add", "origin", "git@github.com:Dannymor94/tg_bot_masterskay.git")

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    log = tmp_path / "push.log"
    fakegit = fakebin / "git"
    fakegit.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "push" ]; then printf "%s\\n" "$*" > "$PUSH_LOG"; exit 0; fi\n'
        'exec "$REAL_GIT" "$@"\n',
        encoding="utf-8",
    )
    fakegit.chmod(0o755)
    environment = dict(os.environ)
    environment["REAL_GIT"] = REAL_GIT
    environment["PUSH_LOG"] = str(log)
    environment["PATH"] = f"{fakebin}{os.pathsep}{environment['PATH']}"
    return repo, log, environment


def _run(repo: Path, environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "branch",
    ["main", "master", "feature/something", "t-danger", "t-123-INVALID", "t-123/foo"],
)
def test_rejects_non_task_branches(
    task_repo: tuple[Path, Path, dict[str, str]], branch: str
) -> None:
    repo, log, environment = task_repo
    if branch != "main":
        _git(repo, "switch", "-c", branch)
    result = _run(repo, environment)
    assert result.returncode == 2
    assert not log.exists()


@pytest.mark.parametrize(
    "branch", ["t-103-evidence-patch", "t-103c-records-403-diagnosis", "t-114"]
)
def test_pushes_only_current_task_branch(
    task_repo: tuple[Path, Path, dict[str, str]], branch: str
) -> None:
    repo, log, environment = task_repo
    _git(repo, "switch", "-c", branch)
    result = _run(repo, environment)
    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8") == "push -u origin HEAD\n"


def test_rejects_unapproved_remote(
    task_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, log, environment = task_repo
    _git(repo, "switch", "-c", "t-123-sample")
    _git(repo, "remote", "set-url", "origin", "git@github.com:another/repo.git")
    result = _run(repo, environment)
    assert result.returncode == 2
    assert not log.exists()


def test_rejects_detached_head(task_repo: tuple[Path, Path, dict[str, str]]) -> None:
    repo, log, environment = task_repo
    _git(repo, "switch", "--detach", "HEAD")
    result = _run(repo, environment)
    assert result.returncode == 2
    assert not log.exists()


def test_rejects_dirty_worktree(task_repo: tuple[Path, Path, dict[str, str]]) -> None:
    repo, log, environment = task_repo
    _git(repo, "switch", "-c", "t-123-sample")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    result = _run(repo, environment)
    assert result.returncode == 2
    assert not log.exists()


def test_rejects_arguments(task_repo: tuple[Path, Path, dict[str, str]]) -> None:
    repo, log, environment = task_repo
    _git(repo, "switch", "-c", "t-123-sample")
    result = _run(repo, environment, "--force")
    assert result.returncode == 2
    assert not log.exists()


def test_rejects_subdirectory(task_repo: tuple[Path, Path, dict[str, str]]) -> None:
    repo, log, environment = task_repo
    _git(repo, "switch", "-c", "t-123-sample")
    (repo / "sub").mkdir()
    result = _run(repo / "sub", environment)
    assert result.returncode == 2
    assert not log.exists()
