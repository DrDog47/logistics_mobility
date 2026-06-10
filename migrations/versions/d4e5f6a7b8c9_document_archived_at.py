"""driver_document / vehicle_document gain archived_at

Outdated document versions are moved to Archive/ on disk and marked archived in
the registry (PRD §8.6) — kept as history, but no longer the current document.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('driver_document', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vehicle_document', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('vehicle_document', 'archived_at')
    op.drop_column('driver_document', 'archived_at')
