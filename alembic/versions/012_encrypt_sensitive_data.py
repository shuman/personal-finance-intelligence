"""encrypt_sensitive_data

Revision ID: 012
Revises: 011
Create Date: 2026-04-24

Encrypts 33 sensitive fields across 14 tables using Fernet encryption.
Adds hash columns for lookup (email_hash, filename_hash, description_hash,
token_hash) and card_last_four for account matching.

Idempotent: safe to re-run if partially applied.

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


def _column_exists(conn, table, column):
    """Check if a column exists in a table."""
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {"table": table, "column": column})
    return result.fetchone() is not None


def _index_exists(conn, index_name):
    """Check if an index exists."""
    result = conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": index_name})
    return result.fetchone() is not None


def _constraint_exists(conn, table_name, constraint_name):
    """Check if a constraint exists."""
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE table_name = :table AND constraint_name = :name"
    ), {"table": table_name, "name": constraint_name})
    return result.fetchone() is not None


def upgrade() -> None:
    encrypt, hash_val = _get_settings()
    conn = op.get_bind()

    # ================================================================
    # PHASE 1: Add new columns (all nullable initially) — idempotent
    # ================================================================
    logger.info("Phase 1: Adding new columns...")

    new_columns = [
        ('users', 'email_hash', sa.String(64)),
        ('statements', 'filename_hash', sa.String(64)),
        ('transactions', 'description_hash', sa.String(64)),
        ('accounts', 'card_last_four', sa.String(4)),
        ('password_reset_tokens', 'token_hash', sa.String(64)),
    ]
    for table, column, col_type in new_columns:
        if not _column_exists(conn, table, column):
            op.add_column(table, sa.Column(column, col_type, nullable=True))
        else:
            logger.info(f"  Column {table}.{column} already exists, skipping")

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
    # PHASE 3: Drop old indexes, set NOT NULL, create new indexes — idempotent
    # ================================================================
    logger.info("Phase 3: Updating indexes and constraints...")

    # --- users ---
    for idx in ['ix_users_email', 'idx_16965_ix_users_email']:
        if _index_exists(conn, idx):
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
    op.alter_column('users', 'email_hash', nullable=False)
    if not _index_exists(conn, 'ix_users_email_hash'):
        op.create_index('ix_users_email_hash', 'users', ['email_hash'], unique=True)

    # --- statements ---
    for idx in ['idx_17008_ix_statements_filename', 'idx_17008_ix_statements_account_number',
                'idx_17008_idx_statement_account_date']:
        if _index_exists(conn, idx):
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
    op.alter_column('statements', 'filename_hash', nullable=False)
    if not _index_exists(conn, 'ix_statements_filename_hash'):
        op.create_index('ix_statements_filename_hash', 'statements', ['filename_hash'], unique=True)

    # --- transactions ---
    for idx in ['idx_16903_sqlite_autoindex_transactions_1',
                'idx_16903_ix_transactions_account_number',
                'idx_16903_ix_transactions_merchant_name',
                'idx_16903_ix_transactions_reference_number',
                'idx_16903_idx_transaction_merchant']:
        if _index_exists(conn, idx):
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
    if not _index_exists(conn, 'ix_transactions_description_hash'):
        op.create_index('ix_transactions_description_hash', 'transactions', ['description_hash'])
    if not _index_exists(conn, 'uq_transaction_duplicate'):
        op.create_index(
            'uq_transaction_duplicate', 'transactions',
            ['statement_id', 'transaction_date', 'description_hash', 'amount'],
            unique=True,
        )

    # --- accounts ---
    if not _index_exists(conn, 'ix_accounts_card_last_four'):
        op.create_index('ix_accounts_card_last_four', 'accounts', ['card_last_four'])

    # --- password_reset_tokens ---
    if _index_exists(conn, 'ix_password_reset_tokens_token'):
        conn.execute(text("DROP INDEX IF EXISTS ix_password_reset_tokens_token"))
    op.alter_column('password_reset_tokens', 'token_hash', nullable=False)
    if not _index_exists(conn, 'ix_password_reset_tokens_token_hash'):
        op.create_index('ix_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'], unique=True)

    # --- payments ---
    if _index_exists(conn, 'idx_16928_ix_payments_account_number'):
        conn.execute(text("DROP INDEX IF EXISTS idx_16928_ix_payments_account_number"))

    # ================================================================
    # PHASE 4: Change Numeric columns to Text for encrypted storage
    # ================================================================
    logger.info("Phase 4: Changing Numeric columns to Text...")

    for col in ['credit_limit', 'available_credit', 'cash_advance_limit', 'new_balance']:
        # Check current type before altering
        result = conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'statements' AND column_name = :col"
        ), {"col": col})
        row = result.fetchone()
        if row and row[0] not in ('text', 'character varying'):
            op.alter_column(
                'statements', col,
                type_=sa.Text(),
                existing_type=sa.Numeric(15, 2),
                postgresql_using=f'{col}::text',
            )

    # ================================================================
    # PHASE 5: Encrypt existing data table by table
    #          Skips values that are already encrypted
    # ================================================================
    logger.info("Phase 5: Encrypting existing data...")

    from cryptography.fernet import Fernet, InvalidToken
    import os

    fernet = Fernet(os.environ['ENCRYPTION_KEY'].encode())

    def _is_encrypted(val):
        """Check if a value is already Fernet-encrypted."""
        if val is None or val == '':
            return True
        try:
            fernet.decrypt(val.encode())
            return True
        except (InvalidToken, Exception):
            return False

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
                if not _is_encrypted(val):
                    updates[col] = encrypt(val)

            if not updates:
                continue

            set_clause = ", ".join(f"{col} = :{col}" for col in updates)
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
        if raw is not None and not _is_encrypted(raw if isinstance(raw, str) else ''):
            if isinstance(raw, dict):
                raw_str = json.dumps(raw, default=str)
            else:
                raw_str = str(raw)
            encrypted = encrypt(raw_str)
            conn.execute(
                text("UPDATE ai_extractions SET raw_response = :val WHERE id = :id"),
                {"val": encrypted, "id": row[0]}
            )

    _encrypt_table_columns('liability_templates', ['name'])
    _encrypt_table_columns('monthly_liabilities', ['name', 'status'])
    _encrypt_table_columns('password_reset_tokens', ['token'])

    logger.info("Migration 012 complete!")


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade not supported for encryption migration. "
        "Restore from database backup if needed."
    )
