"""Tests for the OS-user → DDB-User resolver.

Covers the shared-tablet identity path: every OS user gets a `User`
row (auto-created on first call), subsequent calls return the same row
from the cache, changing the OS user forces a re-lookup after the
cache is cleared, and the row can be re-attached to a fresh session
without extra work.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from ddb import current_user as cu
from ddb.models import User


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cu.clear_cache()
    yield
    cu.clear_cache()


def test_auto_creates_user_on_first_call(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cu, "_os_username", lambda: "alice")
    row = cu.current_user(session)
    assert row.id is not None
    assert row.username == "alice"
    assert row.full_name is None

    # No dupes: a second call for the same OS user hits the cache and
    # doesn't insert a second row.
    row2 = cu.current_user(session)
    assert row2.id == row.id
    all_rows = session.exec(select(User).where(User.username == "alice")).all()
    assert len(all_rows) == 1


def test_reuses_existing_user_row(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre = User(username="bart", full_name="Bart Geurten")
    session.add(pre)
    session.commit()
    session.refresh(pre)

    monkeypatch.setattr(cu, "_os_username", lambda: "bart")
    row = cu.current_user(session)
    assert row.id == pre.id
    assert row.full_name == "Bart Geurten"  # existing metadata untouched


def test_cache_isolates_by_process_not_by_os_lookup(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once a user has been resolved this run, the cached id wins even
    if `_os_username` starts lying — matches the production reality
    where the OS user can't change without a full restart."""
    monkeypatch.setattr(cu, "_os_username", lambda: "alice")
    first = cu.current_user(session)

    monkeypatch.setattr(cu, "_os_username", lambda: "bob")
    second = cu.current_user(session)
    assert second.id == first.id
    assert second.username == "alice"


def test_clear_cache_forces_re_resolution(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cu, "_os_username", lambda: "alice")
    cu.current_user(session)

    monkeypatch.setattr(cu, "_os_username", lambda: "bob")
    cu.clear_cache()
    row = cu.current_user(session)
    assert row.username == "bob"


def test_current_user_id_matches_current_user(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cu, "_os_username", lambda: "alice")
    assert cu.current_user_id(session) == cu.current_user(session).id


def test_cache_survives_across_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache is keyed by row id, not session identity. A second session
    should get the same DB row (re-hydrated) — otherwise every workflow
    call would trigger a fresh SELECT."""
    from sqlmodel import SQLModel, create_engine

    import ddb.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(cu, "_os_username", lambda: "alice")
    # In-memory sqlite: reuse the same engine so both sessions see the
    # same row. The test still exercises the "different Session
    # object" path.
    with Session(engine) as s1:
        first = cu.current_user(s1)
        first_id = first.id
    with Session(engine) as s2:
        second = cu.current_user(s2)
        assert second.id == first_id
