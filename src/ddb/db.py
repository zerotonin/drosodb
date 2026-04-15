from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from ddb.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def init_db() -> None:
    """Create all tables. For dev/tests only — production uses Alembic."""
    import ddb.models  # noqa: F401  (register models)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
