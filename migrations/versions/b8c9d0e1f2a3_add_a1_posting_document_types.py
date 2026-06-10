"""add a1 / posting driver document types to the catalogue (PRD §11.1)

Two new driver document types are part of the minimum-package norm (§11): ``a1``
(A1 social security certificate) and ``posting`` (oświadczenie o delegowaniu —
the IMI posting declaration, distinct from the existing ``oswiadczenie`` work
permit). Insert them into the operator-editable ``document_type`` catalogue for
existing databases; fresh installs get them from BASE_DOCUMENT_TYPES.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-10
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None

_NEW_TYPES = [
    ('a1', 'A1 social security certificate'),
    ('posting', 'Posting declaration (oświadczenie o delegowaniu)'),
]


def upgrade():
    # Idempotent insert — skip rows already seeded (uq on type+entity_type).
    for code, label in _NEW_TYPES:
        op.execute(
            f"""
            INSERT INTO document_type (uuid, created_at, is_deleted, type, entity_type, label)
            VALUES (gen_random_uuid(), now(), false, '{code}', 'driver', '{label}')
            ON CONFLICT (type, entity_type) DO NOTHING
            """
        )


def downgrade():
    op.execute(
        "DELETE FROM document_type "
        "WHERE entity_type = 'driver' AND type IN ('a1', 'posting')"
    )
