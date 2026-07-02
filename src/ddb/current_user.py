"""Resolve the OS user to a DDB `User` row.

Multi-user shared-tablet plan (see README §Multi-user on one tablet):
every human logs into their own Linux account; the DDB actor id for
every workflow call is derived from `getpass.getuser()`. First launch
for a new username auto-creates the row so the user just starts using
the app — no admin step required.

For single-user installs the behaviour is identical: the developer's
Linux username becomes the sole DDB user, its row is created once, and
subsequent calls return the cached row.

The cache is a process-local User instance keyed by the OS username at
process start. Detached rows are re-hydrated against whatever session
the caller passes in so relationships stay usable.
"""

from __future__ import annotations

import getpass

from sqlmodel import Session, select

from ddb.config import settings
from ddb.models import User

_cached_id: int | None = None


def _os_username() -> str:
    """Return the current OS username. Wrapped so tests can monkeypatch it."""
    return getpass.getuser()


def _resolve_username() -> str:
    """Which DDB `User.username` should this session act as?

    Priority:
      1. `settings.actor_username_override` — the identity alias set
         from Settings → Identity. Persisted via .env
         (DDB_ACTOR_USERNAME_OVERRIDE), so a workstation-specific
         mapping is durable across restarts.
      2. `getpass.getuser()` — the OS username, the shared-tablet
         default.

    The override must be a non-empty string to win; empty means auto.
    """
    override = (settings.actor_username_override or "").strip()
    return override or _os_username()


def clear_cache() -> None:
    """Force re-resolution on the next `current_user()` call. Called
    from Settings when the user picks a different identity so the next
    workflow attributes to the new row without a restart."""
    global _cached_id
    _cached_id = None


def current_user(session: Session) -> User:
    """Return the `User` row for the resolved identity, creating it
    only when the OS-user fallback path hits an unknown username.

    If the override points at a name that doesn't exist in the DB,
    that's a misconfiguration (the user picked a stale identity from
    the Settings dropdown before it was populated, or hand-edited
    .env). We DON'T auto-create in that case — silently minting a
    misspelled shadow user is exactly the failure mode this override
    exists to fix. Instead we surface the mismatch by raising, so the
    Settings save handler shows a message and the user can pick again.
    """
    global _cached_id
    if _cached_id is not None:
        row = session.get(User, _cached_id)
        if row is not None:
            return row
        _cached_id = None  # cached row was deleted; fall through to re-resolve

    username = _resolve_username()
    row = session.exec(select(User).where(User.username == username)).first()
    if row is None:
        # Only auto-create when we're on the OS-user fallback. An
        # explicit override to a missing name is a user error.
        override = (settings.actor_username_override or "").strip()
        if override:
            raise LookupError(
                f"actor_username_override={override!r} does not match any "
                "existing DDB user. Fix Settings → Identity or edit "
                "DDB_ACTOR_USERNAME_OVERRIDE in .env."
            )
        row = User(username=username, full_name=None)
        session.add(row)
        session.commit()
        session.refresh(row)
    _cached_id = row.id
    return row


def current_user_id(session: Session) -> int:
    """Convenience: the row's id, guaranteed non-None after `current_user`."""
    row = current_user(session)
    assert row.id is not None
    return row.id
