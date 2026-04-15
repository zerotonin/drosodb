from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_event"

    id: int | None = Field(default=None, primary_key=True)
    actor_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    entity_type: str = Field(index=True)  # 'vial' | 'genotype' | ...
    entity_id: int = Field(index=True)
    action: str = Field(index=True)  # 'create' | 'flip' | 'cross' | 'decommission' | ...
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, index=True)
