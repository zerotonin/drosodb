"""partial unique index on active vial print_code

Revision ID: c270ff9405be
Revises: e9fa7ec2adfe
Create Date: 2026-04-15 16:44:52.370313

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c270ff9405be"
down_revision: str | Sequence[str] | None = "e9fa7ec2adfe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ux_vial_active_print_code",
        "vial",
        ["print_code"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("ux_vial_active_print_code", table_name="vial")
