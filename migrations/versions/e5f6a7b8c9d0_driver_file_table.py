"""driver_file table — normalise driver_document scans into rows

A driver document may have several files (front/back, pages). The
``driver_document.file_links`` JSONB array is replaced by a first-class
``driver_file`` table (one row per file, FK ``document_uuid`` → driver_document),
each row carrying the metadata recognised from that file. ``vehicle_document``
keeps its ``file_links`` array unchanged.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    op.create_table(
        'driver_file',
        sa.Column('document_type', sa.String(length=30), nullable=True),
        sa.Column('document_id', sa.String(length=100), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('file_link', sa.Text(), nullable=False),
        sa.Column('extra', _JSONB, nullable=True),
        sa.Column('document_uuid', sa.Uuid(), nullable=False),
        sa.Column('uuid', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.ForeignKeyConstraint(['document_uuid'], ['driver_document.uuid'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('uuid'),
    )
    with op.batch_alter_table('driver_file', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_driver_file_document_uuid'), ['document_uuid'], unique=False
        )

    # Fold each existing driver_document.file_links element into a driver_file row,
    # copying the document's type/number/dates onto the file.
    op.execute(
        """
        INSERT INTO driver_file
            (uuid, created_at, is_deleted,
             document_type, document_id, start_date, end_date, file_link, document_uuid)
        SELECT gen_random_uuid(), now(), false,
               d.document_type, d.document_id, d.start_date, d.end_date,
               elem.value #>> '{}', d.uuid
        FROM driver_document d
        CROSS JOIN LATERAL jsonb_array_elements(d.file_links) AS elem(value)
        WHERE d.file_links IS NOT NULL AND jsonb_array_length(d.file_links) > 0
        """
    )

    op.drop_column('driver_document', 'file_links')


def downgrade():
    op.add_column('driver_document', sa.Column('file_links', _JSONB, nullable=True))
    # Re-fold driver_file links back into the document array (order by created_at).
    op.execute(
        """
        UPDATE driver_document d
        SET file_links = sub.links
        FROM (
            SELECT document_uuid,
                   jsonb_agg(to_jsonb(file_link) ORDER BY created_at) AS links
            FROM driver_file
            WHERE is_deleted = false
            GROUP BY document_uuid
        ) AS sub
        WHERE d.uuid = sub.document_uuid
        """
    )
    op.drop_table('driver_file')
