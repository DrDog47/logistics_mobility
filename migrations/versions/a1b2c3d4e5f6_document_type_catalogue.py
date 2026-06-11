"""document_type catalogue, composite FK, multi-file links

Adds the operator-editable ``document_type`` catalogue keyed by
``(type, entity_type)``, points ``driver_document`` / ``vehicle_document`` at it
via a composite FK (new ``entity_type`` discriminator column), and replaces the
single ``file_link`` column with a ``file_links`` JSONB array.

Revision ID: a1b2c3d4e5f6
Revises: 67930e7cce7a
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.docs.constants import BASE_DOCUMENT_TYPES

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '67930e7cce7a'
branch_labels = None
depends_on = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    # --- document_type catalogue --------------------------------------------
    document_type = op.create_table(
        'document_type',
        sa.Column('type', sa.String(length=30), nullable=False),
        sa.Column('entity_type', sa.String(length=20), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=True),
        sa.Column('uuid', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.PrimaryKeyConstraint('uuid'),
        sa.UniqueConstraint('type', 'entity_type', name='uq_document_type_type_entity'),
    )

    # Seed the base catalogue (uuid / created_at / is_deleted use DB defaults).
    seed_rows = [
        {'type': code, 'entity_type': entity_type, 'label': label}
        for entity_type, entries in BASE_DOCUMENT_TYPES.items()
        for code, label in entries
    ]
    if seed_rows:
        op.bulk_insert(document_type, seed_rows)

    # --- driver_document: entity_type discriminator + composite FK ----------
    op.add_column(
        'driver_document',
        sa.Column('entity_type', sa.String(length=20), nullable=False, server_default='driver'),
    )
    op.create_foreign_key(
        'fk_driver_document_type',
        'driver_document', 'document_type',
        ['document_type', 'entity_type'], ['type', 'entity_type'],
        ondelete='RESTRICT',
    )

    # --- vehicle_document: entity_type discriminator + composite FK ---------
    op.add_column(
        'vehicle_document',
        sa.Column('entity_type', sa.String(length=20), nullable=False, server_default='vehicle'),
    )
    op.create_foreign_key(
        'fk_vehicle_document_type',
        'vehicle_document', 'document_type',
        ['document_type', 'entity_type'], ['type', 'entity_type'],
        ondelete='RESTRICT',
    )

    # --- file_link (TEXT) -> file_links (JSONB array) -----------------------
    for table in ('driver_document', 'vehicle_document'):
        op.add_column(table, sa.Column('file_links', _JSONB, nullable=True))
        op.execute(
            f"UPDATE {table} SET file_links = jsonb_build_array(file_link) "
            f"WHERE file_link IS NOT NULL"
        )
        op.drop_column(table, 'file_link')


def downgrade():
    for table in ('driver_document', 'vehicle_document'):
        op.add_column(table, sa.Column('file_link', sa.Text(), nullable=True))
        op.execute(
            f"UPDATE {table} SET file_link = file_links->>0 "
            f"WHERE file_links IS NOT NULL AND jsonb_array_length(file_links) > 0"
        )
        op.drop_column(table, 'file_links')

    op.drop_constraint('fk_vehicle_document_type', 'vehicle_document', type_='foreignkey')
    op.drop_column('vehicle_document', 'entity_type')
    op.drop_constraint('fk_driver_document_type', 'driver_document', type_='foreignkey')
    op.drop_column('driver_document', 'entity_type')

    op.drop_table('document_type')
