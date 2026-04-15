import pytest
from sqlmodel import Session, SQLModel, create_engine

import ddb.models  # noqa: F401  (register models with metadata)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
