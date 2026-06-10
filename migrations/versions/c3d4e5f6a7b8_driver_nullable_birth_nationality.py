"""drivers.birth_date and nationality become nullable

A driver auto-created from a recognised passport may lack some fields until an
operator completes the card (PRD §8.4). Both columns drop NOT NULL.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('drivers', 'birth_date', existing_type=sa.Date(), nullable=True)
    op.alter_column('drivers', 'nationality', existing_type=sa.String(length=3), nullable=True)


def downgrade():
    # Backfill NULLs before downgrading.
    op.alter_column('drivers', 'nationality', existing_type=sa.String(length=3), nullable=False)
    op.alter_column('drivers', 'birth_date', existing_type=sa.Date(), nullable=False)
