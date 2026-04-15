"""add generation to vial

Revision ID: 3007f7a8c51c
Revises: 1bb9b367c70f
Create Date: 2026-04-16 00:19:11.656561

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3007f7a8c51c"
down_revision: str | Sequence[str] | None = "1bb9b367c70f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("vial", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("generation", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_index(batch_op.f("ix_vial_generation"), ["generation"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("vial", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_vial_generation"))
        batch_op.drop_column("generation")
