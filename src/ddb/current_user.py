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

from ddb.models import User

_cached_id: int | None = None


def _os_username() -> str:
    """Return the current OS username. Wrapped so tests can monkeypatch it."""
    return getpass.getuser()


def clear_cache() -> None:
    """Force re-resolution on the next `current_user()` call. For tests."""
    global _cached_id
    _cached_id = None


def current_user(session: Session) -> User:
    """Return the `User` row for the OS user, creating it on first call."""
    global _cached_id
    if _cached_id is not None:
        row = session.get(User, _cached_id)
        if row is not None:
            return row
        _cached_id = None  # cached row was deleted; fall through to re-resolve

    username = _os_username()
    row = session.exec(select(User).where(User.username == username)).first()
    if row is None:
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
