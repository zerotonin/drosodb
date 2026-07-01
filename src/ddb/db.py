"""Engine + session factory.

For the shared-tablet install (multiple OS users on one machine hitting
one SQLite file), the connect-time PRAGMAs matter:

  - `journal_mode=WAL`      — readers don't block writers and vice versa;
                              essential the moment two humans use the GUI
                              in overlapping timeframes.
  - `synchronous=NORMAL`    — WAL-safe faster commits; no data loss on
                              app crash, only on OS crash.
  - `busy_timeout=5000`     — five-second grace period on lock contention
                              before raising OperationalError, so a big
                              flip-all commit doesn't kick the printer's
                              status probe out.

For single-user / in-memory-test URLs the pragmas are still applied and
are harmless. Non-SQLite URLs skip the whole block.
"""

from collections.abc import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from ddb.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()


def init_db() -> None:
    """Create all tables. For dev/tests only — production uses Alembic."""
    import ddb.models  # noqa: F401  (register models)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
