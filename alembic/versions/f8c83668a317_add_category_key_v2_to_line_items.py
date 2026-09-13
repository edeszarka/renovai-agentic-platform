"""add category_key_v2 to line_items

Revision ID: f8c83668a317
Revises: 2d3124aea397
Create Date: 2026-09-13 11:30:35.268688

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8c83668a317"
down_revision: Union[str, Sequence[str], None] = "2d3124aea397"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the canonical-taxonomy column.

    ``category_key_v2`` is a plain nullable string, deliberately NOT a foreign
    key to ``work_categories``: the canonical taxonomy extends the seeded keys
    with corpus-driven categories that are not rows in that table.  The legacy
    ``category_key`` column and its historical values are left untouched.
    """
    op.add_column(
        "line_items",
        sa.Column("category_key_v2", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Drop the v2 column, leaving the legacy category_key untouched."""
    op.drop_column("line_items", "category_key_v2")
