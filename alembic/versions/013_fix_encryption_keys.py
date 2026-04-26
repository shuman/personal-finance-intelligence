"""fix_encryption_keys

Revision ID: 013
Revises: 012
Create Date: 2026-04-26

Re-encrypts all data with the correct ENCRYPTION_KEY from .env
and recomputes all hash columns with the correct HASH_SALT.

Idempotent: safe to re-run if partially applied.

IMPORTANT: The environment running this migration must have
ENCRYPTION_KEY and HASH_SALT env vars set to the CORRECT values.
"""
from typing import Sequence, Union
import hashlib
import logging
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = '013'
down_revision: Union[str, Sequence[str], None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Hardcoded test keys that were accidentally used in migration 012
_OLD_KEY = 'uj2BqSoherJttXCo3wyZCQ5FxmEsYdp84v9i5AshV-0='
_OLD_SALT = 'test-salt-value'


def _index_exists(conn, index_name):
    """Check if an index exists."""
    result = conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": index_name})
    return result.fetchone() is not None


def _get_crypto():
    """Build encrypt/decrypt/hash helpers for both old and new keys."""
    from cryptography.fernet import Fernet, InvalidToken

    old_fernet = Fernet(_OLD_KEY.encode())

    new_key = os.environ.get('ENCRYPTION_KEY')
    new_salt = os.environ.get('HASH_SALT', '')

    if not new_key:
        raise RuntimeError(
            "ENCRYPTION_KEY env var is required. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    new_fernet = Fernet(new_key.encode())

    def try_decrypt(val):
        """Try to decrypt with old key first, then new key."""
        if val is None or val == '':
            return val, False
        try:
            return old_fernet.decrypt(val.encode()).decode(), True
        except (InvalidToken, Exception):
            try:
                new_fernet.decrypt(val.encode()).decode()
                return val, False  # Already correct
            except (InvalidToken, Exception):
                # Might be plaintext (production never had test keys)
                logger.warning("  Cannot decrypt value with either key, leaving as-is")
                return val, False

    def new_encrypt(val):
        if val is None or val == '':
            return val
        return new_fernet.encrypt(str(val).encode()).decode()

    def decrypt_for_hash(val):
        """Decrypt a value for hash computation. Tries both keys."""
        if val is None or val == '':
            return None
        try:
            return old_fernet.decrypt(val.encode()).decode()
        except (InvalidToken, Exception):
            pass
        try:
            return new_fernet.decrypt(val.encode()).decode()
        except (InvalidToken, Exception):
            return None

    def new_hash(val):
        if not val:
            return ''
        return hashlib.sha256((str(val) + new_salt).encode()).hexdigest()

    return try_decrypt, new_encrypt, decrypt_for_hash, new_hash


def upgrade() -> None:
    try_decrypt, new_encrypt, decrypt_for_hash, new_hash = _get_crypto()
    conn = op.get_bind()

    # ================================================================
    # PHASE 0: Widen varchar columns that are too narrow for ciphertext
    # ================================================================
    logger.info("Phase 0: Widening varchar columns to text...")

    result = conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'users' AND column_name = 'full_name'"
    ))
    row = result.fetchone()
    if row and row[0] != 'text':
        op.alter_column('users', 'full_name',
                        type_=sa.Text(),
                        existing_type=sa.String(200),
                        postgresql_using='full_name::text')

    # ================================================================
    # PHASE 1: Re-encrypt all encrypted columns
    # ================================================================
    logger.info("Phase 1: Re-encrypting data with correct key...")

    def _reencrypt_columns(table_name, columns):
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        count = result.scalar()
        if count == 0:
            return

        logger.info(f"  Re-encrypting {table_name}: {count} rows, columns: {columns}")

        result = conn.execute(text(f"SELECT id, {', '.join(columns)} FROM {table_name}"))
        rows = result.fetchall()

        for row in rows:
            updates = {}
            for i, col in enumerate(columns):
                val = row[i + 1]
                plaintext, needs_reencrypt = try_decrypt(val)
                if needs_reencrypt:
                    updates[col] = new_encrypt(plaintext)

            if not updates:
                continue

            set_clause = ", ".join(f"{col} = :{col}" for col in updates)
            params = {**updates, "id": row[0]}
            conn.execute(
                text(f"UPDATE {table_name} SET {set_clause} WHERE id = :id"),
                params
            )

    # Re-encrypt all tables
    _reencrypt_columns('users', ['email', 'full_name'])
    _reencrypt_columns('accounts', ['account_number_masked', 'account_nickname'])
    _reencrypt_columns('statements', [
        'account_number', 'cardholder_name', 'filename', 'password',
        'credit_limit', 'available_credit', 'cash_advance_limit', 'new_balance'
    ])
    _reencrypt_columns('transactions', [
        'account_number', 'card_last_four', 'reference_number',
        'authorization_code', 'description_raw', 'description_cleaned',
        'merchant_name'
    ])
    _reencrypt_columns('daily_expenses', ['description_raw', 'description_normalized'])
    _reencrypt_columns('daily_income', ['description_raw', 'description_normalized'])
    _reencrypt_columns('category_summary', ['account_number'])
    _reencrypt_columns('fees', ['account_number'])
    _reencrypt_columns('interest_charges', ['account_number'])
    _reencrypt_columns('payments', ['account_number'])
    _reencrypt_columns('rewards_summary', ['account_number'])

    # ai_extractions.raw_response (JSON)
    result = conn.execute(text("SELECT id, raw_response FROM ai_extractions"))
    for row in result.fetchall():
        raw = row[1]
        if raw is not None:
            plaintext, needs_reencrypt = try_decrypt(raw if isinstance(raw, str) else '')
            if needs_reencrypt:
                encrypted = new_encrypt(plaintext)
                conn.execute(
                    text("UPDATE ai_extractions SET raw_response = :val WHERE id = :id"),
                    {"val": encrypted, "id": row[0]}
                )

    _reencrypt_columns('liability_templates', ['name'])
    _reencrypt_columns('monthly_liabilities', ['name', 'status'])
    _reencrypt_columns('password_reset_tokens', ['token'])

    # ================================================================
    # PHASE 1.5: Merge duplicate users
    # ================================================================
    logger.info("Phase 1.5: Merging duplicate user accounts...")

    result = conn.execute(text("SELECT id, email FROM users ORDER BY id"))
    users = result.fetchall()

    email_to_ids = {}
    for uid, email_cipher in users:
        plaintext = decrypt_for_hash(email_cipher)
        if plaintext:
            email_lower = plaintext.lower()
            if email_lower not in email_to_ids:
                email_to_ids[email_lower] = []
            email_to_ids[email_lower].append(uid)

    for email, user_ids in email_to_ids.items():
        if len(user_ids) <= 1:
            continue

        keep_id = user_ids[0]
        remove_ids = user_ids[1:]

        for remove_id in remove_ids:
            logger.info(f"  Merging user {remove_id} into user {keep_id} (email: {email})")

            conn.execute(text(
                "UPDATE daily_expenses SET user_id = :keep WHERE user_id = :remove"
            ), {"keep": keep_id, "remove": remove_id})

            conn.execute(text(
                "UPDATE daily_income SET user_id = :keep WHERE user_id = :remove"
            ), {"keep": keep_id, "remove": remove_id})

            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": remove_id})

    # ================================================================
    # PHASE 2: Recompute hashes with correct salt — idempotent
    # ================================================================
    logger.info("Phase 2: Recomputing hash columns with correct salt...")

    # Drop unique indexes temporarily (if they exist)
    for idx in ['ix_users_email_hash', 'ix_statements_filename_hash',
                'ix_password_reset_tokens_token_hash']:
        if _index_exists(conn, idx):
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))

    # users.email_hash
    result = conn.execute(text("SELECT id, email FROM users"))
    for row in result.fetchall():
        plaintext = decrypt_for_hash(row[1])
        if plaintext:
            conn.execute(
                text("UPDATE users SET email_hash = :hash WHERE id = :id"),
                {"hash": new_hash(plaintext.lower()), "id": row[0]}
            )

    # statements.filename_hash
    result = conn.execute(text("SELECT id, filename FROM statements"))
    for row in result.fetchall():
        plaintext = decrypt_for_hash(row[1])
        if plaintext:
            conn.execute(
                text("UPDATE statements SET filename_hash = :hash WHERE id = :id"),
                {"hash": new_hash(plaintext), "id": row[0]}
            )

    # transactions.description_hash
    result = conn.execute(text("SELECT id, description_raw FROM transactions"))
    for row in result.fetchall():
        plaintext = decrypt_for_hash(row[1])
        if plaintext:
            conn.execute(
                text("UPDATE transactions SET description_hash = :hash WHERE id = :id"),
                {"hash": new_hash(plaintext), "id": row[0]}
            )

    # password_reset_tokens.token_hash
    result = conn.execute(text("SELECT id, token FROM password_reset_tokens"))
    for row in result.fetchall():
        plaintext = decrypt_for_hash(row[1])
        if plaintext:
            conn.execute(
                text("UPDATE password_reset_tokens SET token_hash = :hash WHERE id = :id"),
                {"hash": new_hash(plaintext), "id": row[0]}
            )

    # Recreate unique indexes (if they don't exist)
    if not _index_exists(conn, 'ix_users_email_hash'):
        op.create_index('ix_users_email_hash', 'users', ['email_hash'], unique=True)
    if not _index_exists(conn, 'ix_statements_filename_hash'):
        op.create_index('ix_statements_filename_hash', 'statements', ['filename_hash'], unique=True)
    if not _index_exists(conn, 'ix_password_reset_tokens_token_hash'):
        op.create_index('ix_password_reset_tokens_token_hash', 'password_reset_tokens', ['token_hash'], unique=True)

    logger.info("Migration 013 complete!")


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade not supported. Restore from database backup if needed."
    )
