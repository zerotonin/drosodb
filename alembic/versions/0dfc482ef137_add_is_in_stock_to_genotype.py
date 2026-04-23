"""add is_in_stock to genotype

Revision ID: 0dfc482ef137
Revises: 3007f7a8c51c
Create Date: 2026-04-23 18:19:23.673812

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0dfc482ef137"
down_revision: str | Sequence[str] | None = "3007f7a8c51c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("genotype", schema=None) as batch_op:
        # server_default="1" so pre-existing rows come through as in_stock
        # (there's no way to distinguish historically dropped strains at
        # migration time — any you actually want out go through the UI).
        batch_op.add_column(
            sa.Column("is_in_stock", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.create_index(batch_op.f("ix_genotype_is_in_stock"), ["is_in_stock"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("genotype", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_genotype_is_in_stock"))
        batch_op.drop_column("is_in_stock")
