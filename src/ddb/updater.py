# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — updater                                                   ║
# ║  « is this clone current, and can it safely catch up? »          ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Git-backed self-update for per-user clones on a shared tablet.  ║
# ║  No Qt here: the GUI wraps these calls in worker threads.        ║
# ║                                                                  ║
# ║  Fast-forward only. Local edits, local commits that diverge,     ║
# ║  and schema migrations are reported, never forced.               ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Check whether this DDB checkout is behind upstream and fast-forward it.

Every tablet user runs their own editable clone, so "update" means a git
fast-forward of that clone plus, when ``pyproject.toml`` changed, an
editable reinstall into the running interpreter's environment.

Schema migrations are deliberately *not* applied here: the SQLite file is
shared by every user, and upgrading it while someone else has an older
GUI open can break their session. :func:`check_schema` reports the state
so the GUI can tell the user to ask an admin instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from ddb import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GIT_TIMEOUT_S = 30
FETCH_TIMEOUT_S = 30
PIP_TIMEOUT_S = 900
MAX_INCOMING_SHOWN = 20

REINSTALL_TRIGGERS: tuple[str, ...] = ("pyproject.toml",)
MIGRATIONS_DIR = "alembic/versions/"
PIP_EXTRAS = "gui,hardware"

# BatchMode makes ssh fail instead of prompting for a passphrase that a
# GUI has no terminal to answer; the user's own GIT_SSH_COMMAND still wins.
DEFAULT_GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=10"


class UpdateError(RuntimeError):
    """A check or update could not run; the message is user-facing."""


# ┌────────────────────────────────────────────────────────────┐
# │ Status types  « what a check and an update report »        │
# └────────────────────────────────────────────────────────────┘


@dataclass(frozen=True)
class UpdateStatus:
    """Snapshot of this clone relative to its upstream branch."""

    branch: str
    upstream: str
    local_sha: str
    local_date: str
    remote_sha: str
    behind: int
    ahead: int
    dirty_files: tuple[str, ...] = ()
    incoming: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()

    @property
    def is_up_to_date(self) -> bool:
        return self.behind == 0

    @property
    def is_diverged(self) -> bool:
        return self.behind > 0 and self.ahead > 0

    @property
    def can_fast_forward(self) -> bool:
        return self.blocking_reason() is None

    @property
    def needs_reinstall(self) -> bool:
        return any(p in REINSTALL_TRIGGERS for p in self.changed_paths)

    @property
    def adds_migrations(self) -> bool:
        return any(p.startswith(MIGRATIONS_DIR) for p in self.changed_paths)

    def blocking_reason(self) -> str | None:
        """Why :func:`apply_update` would refuse, or ``None`` if it can run."""
        if self.is_up_to_date:
            return "Already up to date."
        if self.dirty_files:
            shown = ", ".join(self.dirty_files[:5])
            return (
                f"This copy has local edits to tracked files ({shown}). "
                "Commit or discard them before updating."
            )
        if self.is_diverged:
            return (
                f"This copy has {self.ahead} local commit(s) that are not on "
                f"{self.upstream}. Update it manually with git."
            )
        return None


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of a successful :func:`apply_update`."""

    old_sha: str
    new_sha: str
    reinstalled: bool
    log: str


@dataclass(frozen=True)
class SchemaStatus:
    """Alembic revision of the shared DB versus this checkout's head."""

    db_revision: str | None
    code_head: str | None
    db_is_known: bool = True

    @property
    def needs_upgrade(self) -> bool:
        """DB is on an older revision this code knows how to migrate from."""
        return (
            self.db_revision is not None and self.db_is_known and self.db_revision != self.code_head
        )

    @property
    def db_is_newer(self) -> bool:
        """DB was migrated by newer code than this checkout."""
        return self.db_revision is not None and not self.db_is_known


# ┌────────────────────────────────────────────────────────────┐
# │ Check  « fetch + compare against upstream »                │
# └────────────────────────────────────────────────────────────┘


def is_git_checkout(repo: Path = REPO_ROOT) -> bool:
    return (repo / ".git").exists()


def describe_local_version(repo: Path = REPO_ROOT) -> str:
    """Return e.g. ``0.1.0 · 416045b (2026-07-06)``; bare version off-git."""
    if not is_git_checkout(repo):
        return __version__
    try:
        sha = _git(repo, "rev-parse", "--short", "HEAD")
        date = _git(repo, "log", "-1", "--format=%cs")
    except UpdateError:
        return __version__
    return f"{__version__} · {sha} ({date})"


def check_for_update(repo: Path = REPO_ROOT, *, fetch: bool = True) -> UpdateStatus:
    """Fetch the upstream branch and report how this clone compares.

    Raises:
        UpdateError: not a git checkout, no upstream, or git/network failed.
    """
    if not is_git_checkout(repo):
        raise UpdateError(
            f"{repo} is not a git checkout, so DDB cannot update itself. "
            "Reinstall it with scripts/install_user.sh."
        )
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise UpdateError("This copy is on a detached HEAD; check out a branch first.")
    try:
        upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except UpdateError as e:
        raise UpdateError(f"Branch {branch!r} has no upstream to update from.") from e

    if fetch:
        _git(repo, "fetch", "--quiet", timeout=FETCH_TIMEOUT_S)

    counts = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    ahead, behind = (int(n) for n in counts.split())
    incoming = _git(
        repo, "log", "--format=%h %s", f"-n{MAX_INCOMING_SHOWN}", f"HEAD..{upstream}"
    ).splitlines()
    changed = _git(repo, "diff", "--name-only", f"HEAD...{upstream}").splitlines()
    dirty = [
        line[3:]
        for line in _git(repo, "status", "--porcelain", "--untracked-files=no").splitlines()
    ]
    return UpdateStatus(
        branch=branch,
        upstream=upstream,
        local_sha=_git(repo, "rev-parse", "--short", "HEAD"),
        local_date=_git(repo, "log", "-1", "--format=%cs"),
        remote_sha=_git(repo, "rev-parse", "--short", upstream),
        behind=behind,
        ahead=ahead,
        dirty_files=tuple(dirty),
        incoming=tuple(incoming),
        changed_paths=tuple(changed),
    )


# ┌────────────────────────────────────────────────────────────┐
# │ Apply  « fast-forward pull + optional reinstall »          │
# └────────────────────────────────────────────────────────────┘


def apply_update(repo: Path = REPO_ROOT) -> UpdateResult:
    """Fast-forward this clone to upstream; reinstall if dependencies moved.

    Re-checks first, so a stale status from the GUI can't push a merge
    through after the user edited files in the meantime.

    Raises:
        UpdateError: the update is blocked or a git / pip step failed.
    """
    status = check_for_update(repo)
    reason = status.blocking_reason()
    if reason is not None:
        raise UpdateError(reason)

    log = [_git(repo, "merge", "--ff-only", status.upstream)]
    new_sha = _git(repo, "rev-parse", "--short", "HEAD")
    if status.needs_reinstall:
        try:
            log.append(_reinstall(repo))
        except UpdateError as e:
            raise UpdateError(
                f"Code updated to {new_sha}, but reinstalling dependencies failed: {e}\n"
                f'Run: pip install -e "{repo}[{PIP_EXTRAS}]"'
            ) from e
    return UpdateResult(
        old_sha=status.local_sha,
        new_sha=new_sha,
        reinstalled=status.needs_reinstall,
        log="\n".join(part for part in log if part),
    )


def restart_command() -> list[str]:
    """Command line that relaunches the GUI in the same interpreter."""
    return [sys.executable, "-m", "ddb.cli", "gui"]


def _reinstall(repo: Path) -> str:
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "-e", f"{repo}[{PIP_EXTRAS}]"]
    return _run(cmd, timeout=PIP_TIMEOUT_S, what="pip install")


# ┌────────────────────────────────────────────────────────────┐
# │ Schema  « code head vs shared-DB revision »                │
# └────────────────────────────────────────────────────────────┘


def check_schema(engine: Engine, repo: Path = REPO_ROOT) -> SchemaStatus:
    """Compare the DB's Alembic revision with this checkout's migration head.

    Reads migration scripts from disk, so it reflects a just-pulled update
    without restarting.
    """
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError

    script = ScriptDirectory.from_config(Config(str(repo / "alembic.ini")))
    code_head = script.get_current_head()
    with engine.connect() as conn:
        db_revision = MigrationContext.configure(conn).get_current_revision()

    db_is_known = True
    if db_revision is not None:
        try:
            db_is_known = script.get_revision(db_revision) is not None
        except CommandError:
            db_is_known = False
    return SchemaStatus(db_revision=db_revision, code_head=code_head, db_is_known=db_is_known)


# ┌────────────────────────────────────────────────────────────┐
# │ Git plumbing  « non-interactive, time-boxed subprocess »   │
# └────────────────────────────────────────────────────────────┘


def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT_S) -> str:
    return _run(["git", "-C", str(repo), *args], timeout=timeout, what=f"git {args[0]}")


def _run(cmd: list[str], *, timeout: int, what: str) -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", DEFAULT_GIT_SSH_COMMAND),
    }
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError as e:
        raise UpdateError(f"{cmd[0]} is not installed or not on PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f"{what} timed out after {timeout} s. Is the tablet offline?") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise UpdateError(f"{what} failed: {detail}")
    # rstrip only: porcelain status lines start with a meaningful space.
    return proc.stdout.rstrip()
