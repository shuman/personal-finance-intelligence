"""fix_ai_extractions_column_type

Revision ID: 014
Revises: 013
Create Date: 2026-04-26

Changes ai_extractions.raw_response from json to text
so EncryptedJSON can store encrypted strings.

Idempotent: safe to re-run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '014'
down_revision: Union[str, Sequence[str], None] = '013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check current column type
    result = conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'ai_extractions' AND column_name = 'raw_response'"
    ))
    row = result.fetchone()

    if row and row[0] != 'text':
        op.alter_column(
            'ai_extractions', 'raw_response',
            type_=sa.Text(),
            existing_type=sa.JSON(),
            postgresql_using='raw_response::text',
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade not supported. Restore from database backup if needed."
    )
