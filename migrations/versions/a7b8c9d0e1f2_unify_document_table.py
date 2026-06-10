"""unify driver/vehicle documents into a single ``document`` table

Driver and vehicle documents now share one table via single-table inheritance
(``Document`` base, ``entity_type`` discriminator). This migration renames
``driver_document`` -> ``document``, folds the ``vehicle_document`` rows into it
(adding the vehicle-only ``vehicle_uuid`` / ``file_links`` columns), and drops
``vehicle_document``. ``driver_file.document_uuid`` keeps pointing at the same
rows (the FK follows the table rename).

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    # --- 1. driver_document -> document -------------------------------------
    op.rename_table('driver_document', 'document')

    # Rename the inherited indexes/constraints to the new table's naming so a
    # fresh create_all() and this migrated schema agree (clean autogenerate).
    op.execute('ALTER INDEX ix_driver_document_document_type RENAME TO ix_document_document_type')
    op.execute('ALTER INDEX ix_driver_document_driver_uuid RENAME TO ix_document_driver_uuid')
    op.execute('ALTER TABLE document RENAME CONSTRAINT driver_document_pkey TO document_pkey')
    op.execute('ALTER TABLE document RENAME CONSTRAINT fk_driver_document_type TO fk_document_type')
    op.execute(
        'ALTER TABLE document RENAME CONSTRAINT '
        'driver_document_driver_uuid_fkey TO document_driver_uuid_fkey'
    )

    # driver_uuid is now nullable (vehicle rows leave it NULL).
    op.alter_column('document', 'driver_uuid', existing_type=sa.Uuid(), nullable=True)

    # Index the discriminator (used to scope every subclass query).
    op.create_index(op.f('ix_document_entity_type'), 'document', ['entity_type'], unique=False)

    # --- 2. vehicle-only columns on the shared table ------------------------
    op.add_column('document', sa.Column('vehicle_uuid', sa.Uuid(), nullable=True))
    op.add_column('document', sa.Column('file_links', _JSONB, nullable=True))
    op.create_index(op.f('ix_document_vehicle_uuid'), 'document', ['vehicle_uuid'], unique=False)
    op.create_foreign_key(
        'document_vehicle_uuid_fkey',
        'document', 'vehicles',
        ['vehicle_uuid'], ['uuid'],
        ondelete='RESTRICT',
    )

    # --- 3. fold vehicle_document rows into document ------------------------
    op.execute(
        """
        INSERT INTO document
            (uuid, created_at, deleted_at, is_deleted,
             entity_type, document_type, document_id, start_date, end_date,
             archived_at, extra, vehicle_uuid, file_links)
        SELECT uuid, created_at, deleted_at, is_deleted,
               entity_type, document_type, document_id, start_date, end_date,
               archived_at, extra, vehicle_uuid, file_links
        FROM vehicle_document
        """
    )

    # --- 4. drop vehicle_document ------------------------------------------
    with op.batch_alter_table('vehicle_document', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vehicle_document_vehicle_uuid'))
        batch_op.drop_index(batch_op.f('ix_vehicle_document_document_type'))
    op.drop_table('vehicle_document')


def downgrade():
    # --- 1. recreate vehicle_document --------------------------------------
    op.create_table(
        'vehicle_document',
        sa.Column('vehicle_uuid', sa.Uuid(), nullable=False),
        sa.Column('document_type', sa.String(length=30), nullable=False),
        sa.Column('document_id', sa.String(length=100), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('entity_type', sa.String(length=20), server_default='vehicle', nullable=False),
        sa.Column('file_links', _JSONB, nullable=True),
        sa.Column('extra', _JSONB, nullable=True),
        sa.Column('uuid', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.ForeignKeyConstraint(['vehicle_uuid'], ['vehicles.uuid'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(
            ['document_type', 'entity_type'],
            ['document_type.type', 'document_type.entity_type'],
            name='fk_vehicle_document_type',
            ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('uuid'),
    )
    with op.batch_alter_table('vehicle_document', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vehicle_document_document_type'), ['document_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_vehicle_document_vehicle_uuid'), ['vehicle_uuid'], unique=False)

    # --- 2. move vehicle rows back out of document -------------------------
    op.execute(
        """
        INSERT INTO vehicle_document
            (uuid, created_at, deleted_at, is_deleted,
             entity_type, document_type, document_id, start_date, end_date,
             archived_at, extra, vehicle_uuid, file_links)
        SELECT uuid, created_at, deleted_at, is_deleted,
               entity_type, document_type, document_id, start_date, end_date,
               archived_at, extra, vehicle_uuid, file_links
        FROM document
        WHERE entity_type = 'vehicle'
        """
    )
    op.execute("DELETE FROM document WHERE entity_type = 'vehicle'")

    # --- 3. drop the vehicle-only columns from document --------------------
    op.drop_constraint('document_vehicle_uuid_fkey', 'document', type_='foreignkey')
    op.drop_index(op.f('ix_document_vehicle_uuid'), table_name='document')
    op.drop_column('document', 'file_links')
    op.drop_column('document', 'vehicle_uuid')
    op.drop_index(op.f('ix_document_entity_type'), table_name='document')

    # --- 4. document -> driver_document ------------------------------------
    op.alter_column('document', 'driver_uuid', existing_type=sa.Uuid(), nullable=False)
    op.execute(
        'ALTER TABLE document RENAME CONSTRAINT '
        'document_driver_uuid_fkey TO driver_document_driver_uuid_fkey'
    )
    op.execute('ALTER TABLE document RENAME CONSTRAINT fk_document_type TO fk_driver_document_type')
    op.execute('ALTER TABLE document RENAME CONSTRAINT document_pkey TO driver_document_pkey')
    op.execute('ALTER INDEX ix_document_driver_uuid RENAME TO ix_driver_document_driver_uuid')
    op.execute('ALTER INDEX ix_document_document_type RENAME TO ix_driver_document_document_type')
    op.rename_table('document', 'driver_document')
