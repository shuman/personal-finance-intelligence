"""add_user_consent_fields

Revision ID: 011
Revises: 0c28a4020a73
Create Date: 2026-04-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, Sequence[str], None] = '2df9744e6264'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add consent tracking columns to users table."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('terms_accepted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('privacy_accepted_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('ai_consent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove consent tracking columns from users table."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('ai_consent_at')
        batch_op.drop_column('privacy_accepted_at')
        batch_op.drop_column('terms_accepted_at')
