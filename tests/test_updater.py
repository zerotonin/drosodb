"""Tests for ddb.updater against throwaway local git repos (no network)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from ddb import updater
from ddb.updater import (
    UpdateError,
    apply_update,
    check_for_update,
    check_schema,
    describe_local_version,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

GIT_IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.org")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return out.stdout.strip()


def _commit_file(repo: Path, rel: str, content: str, message: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


class Upstream:
    """A bare origin, a seed repo that pushes to it, and a user clone."""

    def __init__(self, root: Path) -> None:
        self.origin = root / "origin.git"
        self.seed = root / "seed"
        self.clone = root / "clone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.seed)], check=True)
        _commit_file(self.seed, "pyproject.toml", "[project]\nname = 'x'\n", "initial")
        _commit_file(self.seed, "README.md", "hello\n", "readme")
        _git(self.seed, "remote", "add", "origin", str(self.origin))
        _git(self.seed, "push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True)

    def publish(self, rel: str, content: str, message: str) -> None:
        _commit_file(self.seed, rel, content, message)
        _git(self.seed, "push", "-q", "origin", "main")


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    return Upstream(tmp_path)


# ─────────────────────────────────────────────────────────────────
#  Checking
# ─────────────────────────────────────────────────────────────────


def test_fresh_clone_is_up_to_date(upstream: Upstream) -> None:
    status = check_for_update(upstream.clone)
    assert status.is_up_to_date
    assert status.upstream == "origin/main"
    assert status.blocking_reason() == "Already up to date."
    assert not status.can_fast_forward


def test_new_upstream_commit_is_reported(upstream: Upstream) -> None:
    upstream.publish("README.md", "hello again\n", "docs: friendlier greeting")
    status = check_for_update(upstream.clone)
    assert status.behind == 1
    assert status.ahead == 0
    assert status.can_fast_forward
    assert any("friendlier greeting" in line for line in status.incoming)
    assert status.changed_paths == ("README.md",)
    assert not status.needs_reinstall
    assert not status.adds_migrations


def test_dependency_and_migration_changes_are_flagged(upstream: Upstream) -> None:
    upstream.publish("pyproject.toml", "[project]\nname = 'y'\n", "deps")
    upstream.publish("alembic/versions/abc_add_col.py", "# migration\n", "schema")
    status = check_for_update(upstream.clone)
    assert status.needs_reinstall
    assert status.adds_migrations


def test_not_a_git_checkout_raises(tmp_path: Path) -> None:
    with pytest.raises(UpdateError, match="not a git checkout"):
        check_for_update(tmp_path)


def test_describe_local_version_includes_short_sha(upstream: Upstream) -> None:
    sha = _git(upstream.clone, "rev-parse", "--short", "HEAD")
    assert sha in describe_local_version(upstream.clone)


def test_describe_local_version_off_git_is_bare_version(tmp_path: Path) -> None:
    assert describe_local_version(tmp_path) == updater.__version__


# ─────────────────────────────────────────────────────────────────
#  Applying
# ─────────────────────────────────────────────────────────────────


def test_apply_update_fast_forwards(upstream: Upstream) -> None:
    upstream.publish("README.md", "v2\n", "bump")
    result = apply_update(upstream.clone)
    assert result.new_sha == _git(upstream.seed, "rev-parse", "--short", "HEAD")
    assert not result.reinstalled
    assert (upstream.clone / "README.md").read_text(encoding="utf-8") == "v2\n"
    assert check_for_update(upstream.clone).is_up_to_date


def test_apply_update_reinstalls_when_pyproject_changes(
    upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(updater, "_reinstall", lambda repo: calls.append(repo) or "")
    upstream.publish("pyproject.toml", "[project]\nname = 'z'\n", "deps")
    result = apply_update(upstream.clone)
    assert result.reinstalled
    assert calls == [upstream.clone]


def test_apply_update_refuses_local_edits(upstream: Upstream) -> None:
    upstream.publish("README.md", "v2\n", "bump")
    (upstream.clone / "README.md").write_text("my edit\n", encoding="utf-8")
    status = check_for_update(upstream.clone)
    assert status.dirty_files == ("README.md",)
    with pytest.raises(UpdateError, match="local edits"):
        apply_update(upstream.clone)
    assert (upstream.clone / "README.md").read_text(encoding="utf-8") == "my edit\n"


def test_untracked_files_do_not_block(upstream: Upstream) -> None:
    upstream.publish("README.md", "v2\n", "bump")
    (upstream.clone / ".env").write_text("DDB_X=1\n", encoding="utf-8")
    apply_update(upstream.clone)
    assert (upstream.clone / ".env").exists()


def test_apply_update_refuses_diverged_history(upstream: Upstream) -> None:
    upstream.publish("README.md", "v2\n", "upstream change")
    _commit_file(upstream.clone, "notes.txt", "local\n", "local change")
    status = check_for_update(upstream.clone)
    assert status.is_diverged
    with pytest.raises(UpdateError, match="local commit"):
        apply_update(upstream.clone)


def test_failed_reinstall_reports_manual_command(
    upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(repo: Path) -> str:
        raise UpdateError("no network")

    monkeypatch.setattr(updater, "_reinstall", _boom)
    upstream.publish("pyproject.toml", "[project]\nname = 'z'\n", "deps")
    with pytest.raises(UpdateError, match="pip install -e"):
        apply_update(upstream.clone)


# ─────────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────────


def _engine_at_revision(tmp_path: Path, revision: str | None):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.sqlite3'}")
    if revision is not None:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    return engine


def _code_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(
        Config(str(updater.REPO_ROOT / "alembic.ini"))
    ).get_current_head()


def test_schema_current(tmp_path: Path) -> None:
    schema = check_schema(_engine_at_revision(tmp_path, _code_head()))
    assert not schema.needs_upgrade
    assert not schema.db_is_newer


def test_schema_behind_needs_upgrade(tmp_path: Path) -> None:
    schema = check_schema(_engine_at_revision(tmp_path, "e9fa7ec2adfe"))  # initial schema
    assert schema.needs_upgrade
    assert not schema.db_is_newer


def test_schema_from_newer_code_is_flagged(tmp_path: Path) -> None:
    schema = check_schema(_engine_at_revision(tmp_path, "ffffffffffff"))
    assert schema.db_is_newer
    assert not schema.needs_upgrade


def test_unversioned_db_needs_nothing(tmp_path: Path) -> None:
    schema = check_schema(_engine_at_revision(tmp_path, None))
    assert not schema.needs_upgrade
    assert not schema.db_is_newer
