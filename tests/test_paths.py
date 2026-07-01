"""Tests for the local_paths.json resolver.

Covers priority: env var > active profile > fallback, plus the
graceful-degradation guarantees (missing file, broken JSON, wrong
active-profile name all fall back silently to the caller's default).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddb import paths


def _write(local_paths: Path, blob: dict) -> None:
    local_paths.write_text(json.dumps(blob), encoding="utf-8")


@pytest.fixture
def local_paths_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the module's file constant at a per-test temp location.
    Cleans env vars so tests don't leak into each other."""
    f = tmp_path / "local_paths.json"
    monkeypatch.setattr(paths, "_LOCAL_PATHS", f)
    monkeypatch.delenv("DDB_DATABASE_URL", raising=False)
    monkeypatch.delenv("DDB_DATA_DIR", raising=False)
    return f


def test_fallback_when_no_file(local_paths_file: Path) -> None:
    assert paths.resolve_database_url("sqlite:///fallback.db") == "sqlite:///fallback.db"
    assert paths.resolve_data_dir(Path("/tmp/fallback")) == Path("/tmp/fallback")
    assert paths.active_profile_name() == "default"


def test_active_profile_wins_over_fallback(local_paths_file: Path) -> None:
    _write(
        local_paths_file,
        {
            "active_profile": "shared_tablet",
            "profiles": {
                "shared_tablet": {
                    "database_url": "sqlite:////srv/ddb/ddb.sqlite3",
                    "data_root": "/srv/ddb",
                }
            },
        },
    )
    assert paths.resolve_database_url("sqlite:///fallback.db") == "sqlite:////srv/ddb/ddb.sqlite3"
    assert paths.resolve_data_dir(Path("/tmp/fallback")) == Path("/srv/ddb")
    assert paths.active_profile_name() == "shared_tablet"


def test_env_var_wins_over_profile(
    local_paths_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        local_paths_file,
        {
            "active_profile": "shared_tablet",
            "profiles": {
                "shared_tablet": {
                    "database_url": "sqlite:////srv/ddb/ddb.sqlite3",
                    "data_root": "/srv/ddb",
                }
            },
        },
    )
    monkeypatch.setenv("DDB_DATABASE_URL", "sqlite:///env-wins.db")
    monkeypatch.setenv("DDB_DATA_DIR", "/env/wins")
    assert paths.resolve_database_url("sqlite:///fallback.db") == "sqlite:///env-wins.db"
    assert paths.resolve_data_dir(Path("/tmp/fallback")) == Path("/env/wins")


def test_null_profile_values_fall_back(local_paths_file: Path) -> None:
    """A profile that ships with keys=null (the template shape) must be
    treated as 'no override' — otherwise a fresh copy of the template
    would silently blank the fallback."""
    _write(
        local_paths_file,
        {
            "active_profile": "local",
            "profiles": {"local": {"database_url": None, "data_root": None}},
        },
    )
    assert paths.resolve_database_url("sqlite:///fallback.db") == "sqlite:///fallback.db"
    assert paths.resolve_data_dir(Path("/tmp/fallback")) == Path("/tmp/fallback")


def test_missing_active_profile_falls_back(local_paths_file: Path) -> None:
    _write(
        local_paths_file,
        {
            "active_profile": "does-not-exist",
            "profiles": {
                "shared_tablet": {"database_url": "sqlite:///s.db", "data_root": "/s"}
            },
        },
    )
    assert paths.resolve_database_url("sqlite:///fallback.db") == "sqlite:///fallback.db"
    assert paths.resolve_data_dir(Path("/tmp/fallback")) == Path("/tmp/fallback")


def test_broken_json_falls_back(local_paths_file: Path) -> None:
    """Malformed JSON must not wedge the app; the caller sees fallbacks
    and the app can still boot to show an error to the user."""
    local_paths_file.write_text("{ not valid json", encoding="utf-8")
    assert paths.resolve_database_url("sqlite:///fallback.db") == "sqlite:///fallback.db"
    assert paths.resolve_data_dir(Path("/tmp/fallback")) == Path("/tmp/fallback")
    assert paths.active_profile_name() == "default"
