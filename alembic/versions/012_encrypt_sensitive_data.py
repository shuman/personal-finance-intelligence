"""encrypt_sensitive_data

Revision ID: 012
Revises: 011
Create Date: 2026-04-24

Encrypts 33 sensitive fields across 14 tables using Fernet encryption.
Adds hash columns for lookup (email_hash, filename_hash, description_hash,
token_hash) and card_last_four for account matching.

IMPORTANT: The environment running this migration must have
ENCRYPTION_KEY and HASH_SALT env vars set.
"""
from typing import Sequence, Union
import hashlib
import json
import logging

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, Sequence[str], None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_settings():
    """Load encryption key and hash salt from environment."""
    import os
    from cryptography.fernet import Fernet

    encryption_key = os.environ.get('ENCRYPTION_KEY')
    hash_salt = os.environ.get('HASH_SALT', '')

    if not encryption_key:
        raise RuntimeError(
            "ENCRYPTION_KEY env var is required for this migration. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    fernet = Fernet(encryption_key.encode())

    def encrypt(val):
        if val is None or val == '':
            return val
        return fernet.encrypt(str(val).encode()).decode()

    def hash_val(val):
        if not val:
            return ''
        return hashlib.sha256((str(val) + hash_salt).encode()).hexdigest()

    return encrypt, hash_val


def _safe_drop_index(conn, index_name):
    """Drop an index if it exists."""
    conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))


def _safe_drop_constraint(conn, table_name, constraint_name):
    """Drop a constraint if it exists."""
    conn.execute(text(
        f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}"
    ))


def upgrade() -> None:
    encrypt, hash_val = _get_settings()
    conn = op.get_bind()

    # ================================================================
    # PHASE 1: Add new columns (all nullable initially)
    # ================================================================
    logger.info("Phase 1: Adding new columns...")

    op.add_column('users', sa.Column('email_hash', sa.String(64), nullable=True))
    op.add_column('statements', sa.Column('filename_hash', sa.String(64), nullable=True))
    op.add_column('transactions', sa.Column('description_hash', sa.String(64), nullable=True))
    op.add_column('accounts', sa.Column('card_last_four', sa.String(4), nullable=True))
    op.add_column('password_reset_tokens', sa.Column('token_hash', sa.String(64), nullable=True))

    # ================================================================
    # PHASE 2: Populate hash columns and card_last_four
    # ================================================================
    logger.info("Phase 2: Populating hash columns...")

    # users.email_hash
    result = conn.execute(text("SELECT id, email FROM users"))
    for row in result.fetchall():
        conn.execute(
            text("UPDATE users SET email_hash = :hash WHERE id = :id"),
            {"hash": hash_val(row[1].lower() if row[1] else ""), "id": row[0]}
        )

    # statements.filename_hash
    result = conn.execute(text("SELECT id, filename FROM statements"))
    for row in result.fetchall():
        conn.execute(
            text("UPDATE statements SET filename_hash = :hash WHERE id = :id"),
            {"hash": hash_val(row[1] if row[1] else ""), "id": row[0]}
        )

    # transactions.description_hash
    result = conn.execute(text("SELECT id, description_raw FROM transactions"))
    for row in result.fetchall():
        conn.execute(
            text("UPDATE transactions SET description_hash = :hash WHERE id = :id"),
            {"hash": hash_val(row[1] if row[1] else ""), "id": row[0]}
        )

    # accounts.card_last_four — extract last 4 digits from account_number_masked
    result = conn.execute(text("SELECT id, account_number_masked FROM accounts"))
    for row in result.fetchall():
        masked = row[1] or ""
        last_four = masked.replace("*", "").replace("-", "").replace(" ", "")[-4:]
        if last_four:
            conn.execute(
                text("UPDATE accounts SET card_last_four = :lf WHERE id = :id"),
                {"lf": last_four, "id": row[0]}
            )

    # password_reset_tokens.token_hash
    result = conn.execute(text("SELECT id, token FROM password_reset_tokens"))
    for row in result.fetchall():
        conn.execute(
            text("UPDATE password_reset_tokens SET token_hash = :hash WHERE id = :id"),
            {"hash": hash_val(row[1] if row[1] else ""), "id": row[0]}
        )

    # ================================================================
    # PHASE 3: Drop old indexes on columns about to become encrypted,
    #          set NOT NULL, create new indexes
    # ================================================================
    logger.info("Phase 3: Updating indexes and constraints...")

    # --- users ---
    # Drop old unique indexes on email (encrypted, useless as index)
    _safe_drop_index(conn, 'ix_users_email')
    _safe_drop_index(conn, 'idx_16965_ix_users_email')
    # Make email_hash NOT NULL and create unique index
    op.alter_column('users', 'email_hash', nullable=False)
    op.create_index('ix_users_email_hash', 'users', ['email_hash'], unique=True)

    # --- statements ---
    # Drop old unique index on filename
    _safe_drop_index(conn, 'idx_17008_ix_statements_filename')
    # Drop useless index on encrypted account_number
    _safe_drop_index(conn, 'idx_17008_ix_statements_account_number')
    # Drop composite index that includes encrypted account_number
    _safe_drop_index(conn, 'idx_17008_idx_statement_account_date')
    # Make filename_hash NOT NULL and create unique index
    op.alter_column('statements', 'filename_hash', nullable=False)
    op.create_index('ix_statements_filename_hash', 'statements', ['filename_hash'], unique=True)

    # --- transactions ---
    # Drop old unique index on (statement_id, transaction_date, description_raw, amount)
    _safe_drop_index(conn, 'idx_16903_sqlite_autoindex_transactions_1')
    # Drop useless indexes on encrypted columns
    _safe_drop_index(conn, 'idx_16903_ix_transactions_account_number')
    _safe_drop_index(conn, 'idx_16903_ix_transactions_merchant_name')
    _safe_drop_index(conn, 'idx_16903_ix_transactions_reference_number')
    # Drop composite index that includes encrypted merchant_name
    _safe_drop_index(conn, 'idx_16903_idx_transaction_merchant')
    # Create new indexes
    op.create_index('ix_transactions_description_hash', 'transactions', ['description_hash'])
    op.create_index(
        'uq_transaction_duplicate', 'transactions',
        ['statement_id', 'transaction_date', 'description_hash', 'amount'],
        unique=True,
    )

    # --- accounts ---
    op.create_index('ix_accounts_card_last_four', 'accounts', ['card_last_four'])

    # --- password_reset_tokens ---
    # Drop old unique index on token
    _safe_drop_index(conn, 'ix_password_reset_tokens_token')
    # Make token_hash NOT NULL and create unique index
    op.alter_column('password_reset_tokens', 'token_hash', nullable=False)
    op.create_index('ix_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'], unique=True)

    # --- payments ---
    _safe_drop_index(conn, 'idx_16928_ix_payments_account_number')

    # ================================================================
    # PHASE 4: Change Numeric columns to Text for encrypted storage
    # ================================================================
    logger.info("Phase 4: Changing Numeric columns to Text...")

    for col in ['credit_limit', 'available_credit', 'cash_advance_limit', 'new_balance']:
        op.alter_column(
            'statements', col,
            type_=sa.Text(),
            existing_type=sa.Numeric(15, 2),
            postgresql_using=f'{col}::text',
        )

    # ================================================================
    # PHASE 5: Encrypt existing data table by table
    # ================================================================
    logger.info("Phase 5: Encrypting existing data...")

    def _encrypt_table_columns(table_name, columns):
        """Encrypt specified columns in a table."""
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        count = result.scalar()
        if count == 0:
            return

        logger.info(f"  Encrypting {table_name}: {count} rows, columns: {columns}")

        result = conn.execute(text(f"SELECT id, {', '.join(columns)} FROM {table_name}"))
        rows = result.fetchall()

        for row in rows:
            updates = {}
            for i, col in enumerate(columns):
                val = row[i + 1]
                updates[col] = encrypt(val)

            set_clause = ", ".join(f"{col} = :{col}" for col in columns)
            params = {**updates, "id": row[0]}
            conn.execute(
                text(f"UPDATE {table_name} SET {set_clause} WHERE id = :id"),
                params
            )

    # Encrypt each table's sensitive columns
    _encrypt_table_columns('users', ['email', 'full_name'])
    _encrypt_table_columns('accounts', ['account_number_masked', 'account_nickname'])
    _encrypt_table_columns('statements', [
        'account_number', 'cardholder_name', 'filename', 'password',
        'credit_limit', 'available_credit', 'cash_advance_limit', 'new_balance'
    ])
    _encrypt_table_columns('transactions', [
        'account_number', 'card_last_four', 'reference_number',
        'authorization_code', 'description_raw', 'description_cleaned',
        'merchant_name'
    ])
    _encrypt_table_columns('daily_expenses', ['description_raw', 'description_normalized'])
    _encrypt_table_columns('daily_income', ['description_raw', 'description_normalized'])
    _encrypt_table_columns('category_summary', ['account_number'])
    _encrypt_table_columns('fees', ['account_number'])
    _encrypt_table_columns('interest_charges', ['account_number'])
    _encrypt_table_columns('payments', ['account_number'])
    _encrypt_table_columns('rewards_summary', ['account_number'])

    # Encrypt ai_extractions.raw_response (JSON)
    result = conn.execute(text("SELECT id, raw_response FROM ai_extractions"))
    for row in result.fetchall():
        raw = row[1]
        if raw is not None:
            if isinstance(raw, dict):
                raw_str = json.dumps(raw, default=str)
            else:
                raw_str = str(raw)
            encrypted = encrypt(raw_str)
            conn.execute(
                text("UPDATE ai_extractions SET raw_response = :val WHERE id = :id"),
                {"val": encrypted, "id": row[0]}
            )

    # Encrypt liability_templates.name
    _encrypt_table_columns('liability_templates', ['name'])

    # Encrypt monthly_liabilities.name and status
    _encrypt_table_columns('monthly_liabilities', ['name', 'status'])

    # Encrypt password_reset_tokens.token
    _encrypt_table_columns('password_reset_tokens', ['token'])

    logger.info("Migration complete!")


def downgrade() -> None:
    """
    Downgrade is intentionally NOT supported.
    Once data is encrypted, it cannot be safely reversed without
    the original ENCRYPTION_KEY and a full DB backup.
    """
    raise RuntimeError(
        "Downgrade not supported for encryption migration. "
        "Restore from database backup if needed."
    )
