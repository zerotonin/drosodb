"""add wildtype and donor_strain_id to genotype

Revision ID: 1bb9b367c70f
Revises: c270ff9405be
Create Date: 2026-04-15 16:49:33.859563

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1bb9b367c70f"
down_revision: str | Sequence[str] | None = "c270ff9405be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("genotype", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_wildtype", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("donor_strain_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_genotype_donor_strain_id"), ["donor_strain_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_genotype_is_wildtype"), ["is_wildtype"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("genotype", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_genotype_is_wildtype"))
        batch_op.drop_index(batch_op.f("ix_genotype_donor_strain_id"))
        batch_op.drop_column("donor_strain_id")
        batch_op.drop_column("is_wildtype")
