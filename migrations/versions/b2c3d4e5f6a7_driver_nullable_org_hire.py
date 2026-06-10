"""drivers.organisation_uuid and hire_date become nullable

Allows a driver to be auto-created from a recognised passport before an operator
assigns an organisation or hire date (PRD §8.4). Both columns drop NOT NULL.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('drivers', 'organisation_uuid', existing_type=sa.Uuid(), nullable=True)
    op.alter_column('drivers', 'hire_date', existing_type=sa.Date(), nullable=True)


def downgrade():
    # Revert to NOT NULL. Rows with NULLs (e.g. auto-created drivers without an
    # organisation/hire date) must be backfilled before downgrading.
    op.alter_column('drivers', 'hire_date', existing_type=sa.Date(), nullable=False)
    op.alter_column('drivers', 'organisation_uuid', existing_type=sa.Uuid(), nullable=False)
