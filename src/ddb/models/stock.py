from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GenotypeStatus(SQLModel, table=True):
    __tablename__ = "genotype_status"

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    description: str | None = None


class VialStatus(SQLModel, table=True):
    __tablename__ = "vial_status"

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    description: str | None = None
    is_active: bool = True


class Genotype(SQLModel, table=True):
    __tablename__ = "genotype"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    chromosome_x: str | None = None
    chromosome_2: str | None = None
    chromosome_3: str | None = None
    chromosome_4: str | None = None
    chromosome_y: str | None = None
    phenotype: str | None = None
    notes: str | None = None
    is_wildtype: bool = Field(default=False, index=True)
    # False when every vial of this genotype is gone and we're not
    # reconstituting — keeps the row (and its audit / lineage history)
    # but hides it from the New-Vial dropdown and greys it in the
    # Genotypes list. Reports still surface past vials.
    is_in_stock: bool = Field(default=True, index=True)
    donor_strain_id: str | None = Field(default=None, index=True)
    donor_id: int | None = Field(default=None, foreign_key="donor.id")
    created_by_id: int | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=_utcnow)


class GenotypeStatusLink(SQLModel, table=True):
    __tablename__ = "genotype_status_link"

    genotype_id: int = Field(foreign_key="genotype.id", primary_key=True)
    status_id: int = Field(foreign_key="genotype_status.id", primary_key=True)
    set_at: datetime = Field(default_factory=_utcnow, primary_key=True)
    set_by_id: int | None = Field(default=None, foreign_key="user.id")


class Vial(SQLModel, table=True):
    __tablename__ = "vial"

    id: int | None = Field(default=None, primary_key=True)
    print_code: str = Field(index=True)
    genotype_id: int = Field(foreign_key="genotype.id", index=True)
    org_unit_id: int | None = Field(default=None, foreign_key="org_unit.id")
    owner_id: int | None = Field(default=None, foreign_key="user.id")
    is_active: bool = Field(default=True, index=True)
    flipped_from_id: int | None = Field(default=None, foreign_key="vial.id")
    # 1 = original; incremented on every flip / copy.
    generation: int = Field(default=1, index=True)
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    decommissioned_at: datetime | None = None


class VialStatusLink(SQLModel, table=True):
    __tablename__ = "vial_status_link"

    vial_id: int = Field(foreign_key="vial.id", primary_key=True)
    status_id: int = Field(foreign_key="vial_status.id", primary_key=True)
    set_at: datetime = Field(default_factory=_utcnow, primary_key=True)
    set_by_id: int | None = Field(default=None, foreign_key="user.id")


class VialParentage(SQLModel, table=True):
    """Edge table for vial lineage. Two rows per cross (one per parent)."""

    __tablename__ = "vial_parentage"

    child_id: int = Field(foreign_key="vial.id", primary_key=True)
    parent_id: int = Field(foreign_key="vial.id", primary_key=True)
    role: str = Field(default="parent", primary_key=True)  # 'mother' | 'father' | 'parent'
    created_at: datetime = Field(default_factory=_utcnow)
