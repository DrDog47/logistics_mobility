"""drop driver_contracts (contracts are now employment documents)

Contracts moved into the document system: a contract is a ``driver_document`` of
type ``employment`` whose terms live in ``extra``. The bespoke ``driver_contracts``
table is removed (it held no rows). Downgrade recreates the original schema.

Revision ID: f6a7b8c9d0e1
Revises: e70e4340e789
Create Date: 2026-06-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e70e4340e789'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('driver_contracts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_driver_contracts_driver_id'))
    op.drop_table('driver_contracts')


def downgrade():
    op.create_table(
        'driver_contracts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('driver_id', sa.Uuid(), nullable=False),
        sa.Column(
            'contract_type',
            sa.Enum('UMOWA_O_PRACE', 'UMOWA_ZLECENIA', 'B2B', name='contracttype'),
            nullable=False,
        ),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('base_salary_pln', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('hours_norm', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['driver_id'], ['drivers.uuid'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('driver_contracts', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_driver_contracts_driver_id'), ['driver_id'], unique=False
        )
