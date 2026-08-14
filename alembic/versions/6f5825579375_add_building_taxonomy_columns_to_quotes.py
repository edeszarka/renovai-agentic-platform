"""add building taxonomy columns to quotes

Revision ID: 6f5825579375
Revises: 13b971e8e43f
Create Date: 2026-08-14 11:19:09.711571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f5825579375'
down_revision: Union[str, Sequence[str], None] = '13b971e8e43f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('quotes', sa.Column('building_type', sa.Enum('TEGLA', 'PANEL', 'CSUSZOZSALUS', 'KONNYUSZERKEZETES', 'TEGLA_CSALADI_HAZ', 'VALYOG_VEGYES', name='buildingtype'), nullable=True))
    op.add_column('quotes', sa.Column('building_era', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('quotes', 'building_era')
    op.drop_column('quotes', 'building_type')
