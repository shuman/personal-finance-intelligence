"""Add statement_format_hint column to financial_institutions

Revision ID: 0002_add_statement_format_hint
Revises: 0001_initial
Create Date: 2026-04-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0002_add_statement_format_hint"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the column as nullable first so existing rows are not rejected
    op.add_column(
        "financial_institutions",
        sa.Column(
            "statement_format_hint",
            sa.String(50),
            nullable=True,
        ),
    )

    # Back-fill existing rows with the default value
    op.execute(
        "UPDATE financial_institutions SET statement_format_hint = 'generic' "
        "WHERE statement_format_hint IS NULL"
    )

    # Now tighten the column to NOT NULL with a server-side default
    op.alter_column(
        "financial_institutions",
        "statement_format_hint",
        nullable=False,
        server_default="generic",
    )


def downgrade() -> None:
    op.drop_column("financial_institutions", "statement_format_hint")
