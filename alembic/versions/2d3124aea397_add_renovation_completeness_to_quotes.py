"""add renovation_completeness to quotes

Revision ID: 2d3124aea397
Revises: 6f5825579375
Create Date: 2026-08-14 22:05:59.700432

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d3124aea397"
down_revision: Union[str, Sequence[str], None] = "6f5825579375"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "quotes", sa.Column("renovation_completeness", sa.String(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("quotes", "renovation_completeness")
