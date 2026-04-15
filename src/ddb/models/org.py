from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OrgUnit(SQLModel, table=True):
    __tablename__ = "org_unit"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class User(SQLModel, table=True):
    __tablename__ = "user"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str | None = None
    email: str | None = None
    org_unit_id: int | None = Field(default=None, foreign_key="org_unit.id")
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class Donor(SQLModel, table=True):
    """External source of a genotype (lab, stock center, colleague)."""

    __tablename__ = "donor"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    institution: str | None = None
    contact: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
