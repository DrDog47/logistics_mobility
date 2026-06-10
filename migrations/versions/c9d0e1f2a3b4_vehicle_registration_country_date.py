"""vehicles gain registration_country and registration_date

The vehicle registration certificate (tech_passport) carries the country that
issued the registration and the registration date (Vehicle PRD §3.1, §8.4).
``registration_country`` is ISO 3166-1 alpha-3 (same convention as
``drivers.nationality``); both columns are nullable.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('vehicles', sa.Column('registration_country', sa.String(length=3), nullable=True))
    op.add_column('vehicles', sa.Column('registration_date', sa.Date(), nullable=True))


def downgrade():
    op.drop_column('vehicles', 'registration_date')
    op.drop_column('vehicles', 'registration_country')
