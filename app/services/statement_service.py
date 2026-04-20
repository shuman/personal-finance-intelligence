"""
Statement processing service.
Handles complete workflow from PDF upload to database storage.

Extraction pipeline:
  1. Claude Vision (preferred) — universal, multi-bank, multi-card
  2. Regex parser fallback (Amex only, kept for backward compat)
"""
import os
import hashlib
import shutil
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple

from sqlalchemy import select, delete as sa_delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import (
    Statement, Transaction, Fee, InterestCharge,
    RewardsSummary, CategorySummary, Payment, AiExtraction,
    Account, FinancialInstitution,
)
from app.parsers import ParserFactory, AmexParser
from app.config import settings
from app.utils.categorization import calculate_category_summary

logger = logging.getLogger(__name__)


class StatementService:
    """
    Service for processing financial statement PDFs.

    Upload → Detect Bank → Claude Vision Extraction → Category Engine
    → Store → Generate Insights
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.upload_dir = settings.upload_dir
        os.makedirs(self.upload_dir, exist_ok=True)

    @staticmethod
    def _detect_card_type(account_number: str) -> Optional[str]:
        """Detect card network from the first digit of the account/card number."""
        if not account_number or account_number == "unknown":
            return None
        for ch in account_number:
            if ch.isdigit():
                if ch == "3":
                    return "AMEX"
                if ch == "4":
                    return "VISA"
                if ch == "5":
                    return "MASTERCARD"
                return None
        return None

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------

    async def process_statement(
        self,
        file_content: bytes,
        filename: str,
        user_id: int,
        password: Optional[str] = None,
        bank_name: str = "Amex",
        account_id: Optional[int] = None,
        use_claude_vision: bool = True,
        use_extraction_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Main method to process an uploaded statement PDF.

        Returns processing summary with statement ID and statistics.
        Raises ValueError for duplicates or parse failures.
        """
        file_hash = hashlib.sha256(file_content).hexdigest()

        existing = await self._check_duplicate_filename(filename, user_id)
        if existing:
            raise ValueError(
                f"Statement with filename '{filename}' already exists (ID: {existing.id})"
            )
        existing = await self._check_duplicate_hash(file_hash, user_id)
        if existing:
            raise ValueError(
                f"Identical statement file already uploaded (ID: {existing.id})"
            )

        file_path = os.path.join(self.upload_dir, filename)
        with open(file_path, "wb") as f:
            f.write(file_content)

        try:
            working_file = file_path
            if password:
                parser = AmexParser()
                try:
                    working_file = parser.decrypt_pdf(file_path, password)
                except Exception as e:
                    raise ValueError(f"Failed to decrypt PDF: {e}")

            # Detect institution for bank-specific hints
            institution = await self._detect_institution(working_file, bank_name)

            # Try Claude Vision first if API key available
            parsed_data = None
            extraction_method = "regex_fallback"

            if use_claude_vision and settings.anthropic_api_key:
                try:
                    parsed_data = await self._extract_with_claude_vision(
                        user_id,
                        working_file,
                        institution,
                        file_hash=file_hash,
                        use_extraction_cache=use_extraction_cache,
                    )
                    extraction_method = "claude_vision"
                except Exception as e:
                    logger.warning(
                        f"Claude Vision failed, falling back to regex: {e}"
                    )

            if parsed_data is None:
                # Regex fallback
                parser = ParserFactory.get_parser(working_file, bank_name)
                parsed_data = parser.parse(working_file)
                parsed_data["extraction_method"] = "regex_fallback"

            # Use Claude-extracted bank_name if available
            if parsed_data.get("metadata", {}).get("bank_name") not in (None, "Unknown", ""):
                bank_name = parsed_data["metadata"]["bank_name"]

            statement_id, stats = await self._store_parsed_data(
                user_id, parsed_data, filename, file_path, file_hash, password,
                bank_name, account_id, extraction_method
            )

            # Store AiExtraction audit record if vision was used
            if extraction_method == "claude_vision" and parsed_data.get("_ai_extraction"):
                ai_ext_data = parsed_data["_ai_extraction"]
                await self._store_ai_extraction(user_id, statement_id, ai_ext_data, file_hash=file_hash)

            return {
                "statement_id": statement_id,
                "filename": filename,
                "bank_name": bank_name,
                "extraction_method": extraction_method,
                "transactions_added": stats["transactions_added"],
                "transactions_skipped": stats["transactions_skipped"],
                "fees_added": stats["fees_added"],
                "statement_date": stats["statement_date"],
                "total_amount": stats["total_amount"],
                "unmatched_cards": parsed_data.get("unmatched_cards", []),
                "message": "Statement processed successfully",
            }

        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise ValueError(f"Error processing statement: {str(e)}")

    async def save_previewed_data(self, data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
        """
        Save previewed and edited statement data to database.
        Expects the edited data from the preview endpoint.
        """
        from datetime import date as dt_date, datetime as dt

        filename = data["filename"]
        file_hash = data["file_hash"]
        password = data.get("password")
        bank_name = data["bank_name"]
        metadata = data["metadata"]
        transactions = data["transactions"]
        fees = data.get("fees", [])
        account_id = data.get("account_id")

        # Re-upload support: soft-delete old statement first, permanently
        # delete only after the new data has been committed successfully.
        existing = await self._check_duplicate_hash(file_hash, user_id)
        if not existing:
            existing = await self._check_duplicate_filename(filename, user_id)

        old_statement_id = None
        if existing:
            old_statement_id = existing.id
            logger.info(
                f"Re-upload detected (statement id={old_statement_id}), "
                "soft-deleting old data (unique fields renamed)…"
            )
            # Rename unique fields so the new statement can be inserted.
            # Guard against double-prefixing if a previous attempt already
            # soft-deleted this record but the session was later rolled back
            # and the rename was left committed.
            if not existing.filename.startswith("__deleted__"):
                existing.filename = f"__deleted__{existing.id}__{existing.filename}"
            if not existing.pdf_hash.startswith("__deleted__"):
                existing.pdf_hash = f"__deleted__{existing.id}__{existing.pdf_hash}"
            await self.db.flush()

        temp_path = data.get("temp_path")
        if temp_path and os.path.exists(temp_path):
            file_path = os.path.join(self.upload_dir, filename)
            shutil.copy(temp_path, file_path)
        else:
            raise ValueError("Temporary file not found")

        # Convert string dates back to date objects
        for key in ["statement_date", "statement_period_from", "statement_period_to", "payment_due_date"]:
            if metadata.get(key):
                try:
                    metadata[key] = dt.fromisoformat(metadata[key]).date()
                except Exception:
                    metadata[key] = None

        # Convert integer strings to int
        integer_fields = [
            "member_since", "billing_cycle",
            "rewards_opening", "rewards_earned", "rewards_redeemed", "rewards_closing",
        ]
        for field in integer_fields:
            if metadata.get(field) is not None:
                try:
                    metadata[field] = int(float(str(metadata[field]).replace(',', '')))
                except Exception:
                    metadata[field] = None

        # Convert numeric strings to Decimal
        numeric_fields = [
            "previous_balance", "payments_credits", "purchases", "cash_advances",
            "fees_charged", "interest_charged", "adjustments", "new_balance",
            "total_amount_due", "minimum_payment_due", "credit_limit",
            "available_credit", "cash_advance_limit", "credit_utilization_pct",
            "rewards_value_inr",
        ]
        for field in numeric_fields:
            if metadata.get(field) is not None:
                try:
                    metadata[field] = Decimal(str(metadata[field]))
                except Exception:
                    metadata[field] = None

        self._validate_required_fields(metadata)

        # Use Claude-extracted bank_name if better than parameter default
        effective_bank_name = bank_name
        if metadata.get("bank_name") and metadata["bank_name"] not in ("Unknown", ""):
            effective_bank_name = metadata["bank_name"]

        # Detect card type from account number prefix
        card_type = self._detect_card_type(metadata.get("account_number", ""))

        # Calculate credit utilization if not already provided
        if not metadata.get("credit_utilization_pct"):
            new_bal = metadata.get("new_balance")
            credit_lim = metadata.get("credit_limit")
            if new_bal and credit_lim and credit_lim > 0:
                metadata["credit_utilization_pct"] = round(
                    (Decimal(str(new_bal)) / Decimal(str(credit_lim))) * 100, 2
                )

        # Remove fields already passed explicitly so **metadata doesn't duplicate them
        _EXPLICIT_STATEMENT_FIELDS = {"bank_name", "account_id", "extraction_method", "ai_confidence"}
        safe_meta = {k: v for k, v in metadata.items() if k not in _EXPLICIT_STATEMENT_FIELDS}

        statement = Statement(
            uuid=str(uuid.uuid4()),
            filename=filename,
            pdf_hash=file_hash,
            file_path=file_path,
            password=password,
            bank_name=effective_bank_name,
            card_type=card_type,
            account_id=account_id,
            extraction_method=data.get("extraction_method", "regex_fallback"),
            user_id=user_id,
            **safe_meta,
        )
        self.db.add(statement)
        await self.db.flush()

        transactions_added = 0
        transactions_skipped = 0
        duplicate_transactions = []
        successfully_added_transactions = []

        for txn_data in transactions:
            if txn_data.get("transaction_date"):
                txn_data["transaction_date"] = dt.fromisoformat(txn_data["transaction_date"]).date()
            if txn_data.get("posting_date"):
                txn_data["posting_date"] = dt.fromisoformat(txn_data["posting_date"]).date()
            for field in ["amount", "billing_amount", "original_amount", "foreign_amount", "exchange_rate", "fx_rate_applied"]:
                if txn_data.get(field) is not None:
                    try:
                        txn_data[field] = Decimal(str(txn_data[field]))
                    except Exception:
                        txn_data[field] = None

        for txn_data in transactions:
            # Use per-transaction account_id/account_number from card sections
            txn_account_id = txn_data.get("account_id") or account_id
            txn_account_number = txn_data.get("account_number") or statement.account_number
            txn_fields = {k: v for k, v in txn_data.items() if k not in ("account_number", "statement_id", "account_id")}
            transaction = Transaction(
                statement_id=statement.id,
                account_number=txn_account_number,
                account_id=txn_account_id,
                user_id=user_id,
                **txn_fields,
            )
            self.db.add(transaction)
            transactions_added += 1
            successfully_added_transactions.append(txn_data)

        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            transactions_added = 0
            successfully_added_transactions = []

            # Re-apply soft delete after rollback (rollback restored old unique fields)
            if old_statement_id:
                old_stmt = await self.db.execute(
                    select(Statement).where(Statement.id == old_statement_id)
                )
                old_stmt_obj = old_stmt.scalar_one_or_none()
                if old_stmt_obj:
                    old_stmt_obj.filename = f"__deleted__{old_statement_id}__{old_stmt_obj.filename}"
                    old_stmt_obj.pdf_hash = f"__deleted__{old_statement_id}__{old_stmt_obj.pdf_hash}"
                    await self.db.flush()

            statement = Statement(
                uuid=str(uuid.uuid4()),
                filename=filename,
                pdf_hash=file_hash,
                file_path=file_path,
                password=password,
                bank_name=effective_bank_name,
                card_type=card_type,
                account_id=account_id,
                extraction_method=data.get("extraction_method", "regex_fallback"),
                user_id=user_id,
                **safe_meta,
            )
            self.db.add(statement)
            await self.db.flush()

            for idx, txn_data in enumerate(transactions, 1):
                try:
                    txn_account_id = txn_data.get("account_id") or account_id
                    txn_account_number = txn_data.get("account_number") or statement.account_number
                    txn_fields = {k: v for k, v in txn_data.items() if k not in ("account_number", "statement_id", "account_id")}
                    transaction = Transaction(
                        statement_id=statement.id,
                        account_number=txn_account_number,
                        account_id=txn_account_id,
                        user_id=user_id,
                        **txn_fields,
                    )
                    self.db.add(transaction)
                    await self.db.flush()
                    transactions_added += 1
                    successfully_added_transactions.append(txn_data)
                except IntegrityError:
                    transactions_skipped += 1
                    txn_date = txn_data.get("transaction_date")
                    duplicate_transactions.append({
                        "index": idx,
                        "date": txn_date.isoformat() if hasattr(txn_date, "isoformat") else str(txn_date),
                        "description": str(txn_data.get("description_raw", ""))[:50],
                        "amount": float(txn_data.get("amount", 0) or 0),
                    })
                    await self.db.rollback()
                    await self.db.flush()

        if fees:
            for fee_data in fees:
                if fee_data.get("amount"):
                    fee_data["amount"] = Decimal(str(fee_data["amount"]))
                fee = Fee(
                    statement_id=statement.id,
                    account_number=statement.account_number,
                    fee_date=statement.statement_date,
                    user_id=user_id,
                    **fee_data,
                )
                self.db.add(fee)

        if transactions_skipped > 0 and transactions_added == 0:
            await self.db.rollback()
            raise ValueError(
                f"All {len(transactions)} transactions are duplicates. "
                "This file may have already been uploaded."
            )

        try:
            await self._store_category_summaries(statement, successfully_added_transactions)
        except Exception as e:
            await self.db.rollback()
            raise ValueError(f"Error creating category summaries: {str(e)}")

        await self.db.commit()

        # Permanently delete the old (soft-deleted) statement now that new data is committed
        if old_statement_id:
            try:
                old_stmt = await self.db.execute(
                    select(Statement).where(Statement.id == old_statement_id)
                )
                old_stmt_obj = old_stmt.scalar_one_or_none()
                if old_stmt_obj:
                    await self._delete_statement_cascade(old_stmt_obj)
                    await self.db.commit()
                    logger.info(f"Permanently deleted old statement id={old_statement_id}")
            except Exception as e:
                logger.warning(f"Failed to clean up old statement id={old_statement_id}: {e}")
                # Non-fatal — new data is already saved

        result = {
            "statement_id": statement.id,
            "filename": filename,
            "bank_name": bank_name,
            "transactions_added": transactions_added,
            "transactions_skipped": transactions_skipped,
            "statement_date": statement.statement_date.isoformat() if statement.statement_date else None,
            "total_amount": float(statement.total_amount_due) if statement.total_amount_due else None,
            "message": "Statement saved successfully",
        }
        if transactions_skipped > 0:
            result["warning"] = f"{transactions_skipped} duplicate transaction(s) skipped."
            if duplicate_transactions:
                result["duplicate_details"] = duplicate_transactions
        return result

    # ------------------------------------------------------------------
    # Claude Vision pipeline
    # ------------------------------------------------------------------

    async def _extract_with_claude_vision(
        self,
        user_id: int,
        pdf_path: str,
        institution=None,
        file_hash: Optional[str] = None,
        model: Optional[str] = None,
        use_extraction_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Run the full Claude Vision extraction pipeline.
        Returns parsed_data dict compatible with _store_parsed_data.

        If file_hash is provided and use_extraction_cache is True:
        - Checks the ai_extractions cache first (zero API cost on hit)
        - Stores result in cache after a fresh extraction
        """
        # ── Cache lookup ──────────────────────────────────────────────
        if file_hash and use_extraction_cache:
            cached = await self._get_cached_extraction(user_id, file_hash)
            if cached:
                logger.info(
                    f"Cache HIT for file_hash={file_hash[:12]}… "
                    f"(ai_extractions id={cached.id}, "
                    f"originally ${float(cached.cost_usd or 0):.4f} USD)"
                )
                # Re-attach audit metadata (mark as cached, cost = 0 this time)
                payload = dict(cached.raw_response)

                # Re-run account resolution for any cards that failed to
                # register during the original extraction (e.g. if the
                # accounts table wasn't seeded yet or a flush error occurred).
                unmatched = list(payload.get("unmatched_cards", []))
                if unmatched:
                    from app.services.vision.data_normalizer import DataNormalizer
                    normalizer = DataNormalizer(self.db, institution=institution)
                    still_unmatched = []
                    for card in unmatched:
                        aid, existed, auto_reg = await normalizer._resolve_account(
                            user_id,
                            card.get("card_number_masked", ""),
                            card.get("cardholder_name", ""),
                        )
                        if aid:
                            for sec in payload.get("card_sections_meta", []):
                                if sec.get("card_number_masked") == card.get("card_number_masked"):
                                    sec["account_id"] = aid
                                    sec["auto_registered"] = auto_reg
                        else:
                            still_unmatched.append(card)
                    payload["unmatched_cards"] = still_unmatched

                    # Update the cache so future loads don't re-run resolution
                    cached.raw_response = self._make_json_safe(
                        {k: v for k, v in payload.items() if k != "_ai_extraction"}
                    )
                    await self.db.flush()

                # Filter out stale validation errors that were fixed after
                # the original extraction (e.g. accelerated_tiers list→dict).
                raw_issues = cached.issues_flagged or []
                cleaned_issues = [
                    i for i in raw_issues
                    if "accelerated_tiers" not in str(i.get("detail", ""))
                    and "float_parsing" not in str(i.get("detail", ""))
                ]
                # Persist the cleanup so it doesn't re-appear
                if len(cleaned_issues) != len(raw_issues):
                    cached.issues_flagged = cleaned_issues
                    await self.db.flush()

                payload["_ai_extraction"] = {
                    "model_used": cached.model_used,
                    "pages_processed": cached.pages_processed,
                    "pages_skipped": cached.pages_skipped,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "extraction_confidence": float(cached.extraction_confidence or 1.0),
                    "issues_flagged": cleaned_issues,
                    "raw_response": None,
                    "from_cache": True,
                    "cached_at": cached.created_at.isoformat(),
                    "original_cost_usd": float(cached.cost_usd or 0),
                    "original_tokens": cached.input_tokens + cached.output_tokens,
                }
                return payload

        # ── Fresh extraction ──────────────────────────────────────────
        from app.services.vision.claude_extractor import ClaudeExtractor
        from app.services.vision.data_normalizer import DataNormalizer

        # 1. Read PDF bytes and send directly to Claude as a document block
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        logger.info(
            f"PDF extraction pipeline: {len(pdf_bytes) / 1024:.1f} KB → Claude {model or settings.extraction_model}"
        )

        extractor = ClaudeExtractor(institution=institution, model=model or None)
        extraction_result = extractor.extract(pdf_bytes)

        pages_processed = len([p for p in extraction_result.pages if not p.skip])
        pages_skipped   = extraction_result.pages_skipped

        # 2. Normalize to DB-ready format (pass institution for auto-registration currency)
        normalizer = DataNormalizer(self.db, institution=institution)
        normalized = await normalizer.normalize(user_id, extraction_result, "", "")

        ai_meta = {
            "model_used": extraction_result.model_used,
            "pages_processed": pages_processed,
            "pages_skipped": pages_skipped,
            "input_tokens": extraction_result.input_tokens,
            "output_tokens": extraction_result.output_tokens,
            "cost_usd": float(extraction_result.cost_usd or 0),
            "extraction_confidence": float(extraction_result.confidence or 1.0),
            "issues_flagged": extraction_result.issues or [],
            "raw_response": None,
            "from_cache": False,
            "preflight": {},
        }
        normalized["_ai_extraction"] = ai_meta

        # ── Store in cache ────────────────────────────────────────────
        if file_hash:
            await self._store_extraction_cache(user_id, file_hash, normalized, ai_meta)

        return normalized

    # ------------------------------------------------------------------
    # Extraction cache helpers
    # ------------------------------------------------------------------

    async def _get_cached_extraction(self, user_id: int, file_hash: str) -> Optional[AiExtraction]:
        """
        Return the most recent AiExtraction row for this file_hash that has
        a non-null raw_response (i.e. a complete cached payload).
        """
        result = await self.db.execute(
            select(AiExtraction)
            .where(
                AiExtraction.user_id == user_id,
                AiExtraction.file_hash == file_hash,
                AiExtraction.raw_response.isnot(None),
            )
            .order_by(AiExtraction.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _store_extraction_cache(
        self,
        user_id: int,
        file_hash: str,
        parsed_data: Dict[str, Any],
        ai_meta: Dict[str, Any],
    ) -> None:
        """
        Persist a JSON-safe snapshot of parsed_data in ai_extractions
        so future uploads of the same PDF skip the Claude API call.
        """
        from decimal import Decimal

        # Serialize to JSON-safe dict (dates → ISO strings, Decimals → floats)
        safe_payload = self._make_json_safe(
            {k: v for k, v in parsed_data.items() if k != "_ai_extraction"}
        )

        record = AiExtraction(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            file_hash=file_hash,
            statement_id=None,          # Not linked to a statement yet
            model_used=ai_meta.get("model_used", "claude-sonnet-4-5"),
            pages_processed=ai_meta.get("pages_processed", 0),
            pages_skipped=ai_meta.get("pages_skipped", 0),
            input_tokens=ai_meta.get("input_tokens", 0),
            output_tokens=ai_meta.get("output_tokens", 0),
            cost_usd=Decimal(str(ai_meta.get("cost_usd", 0))),
            extraction_confidence=Decimal(str(ai_meta.get("extraction_confidence", 1.0))),
            issues_flagged=ai_meta.get("issues_flagged", []),
            raw_response=safe_payload,
        )
        self.db.add(record)
        await self.db.flush()
        logger.info(
            f"Cached extraction for file_hash={file_hash[:12]}… "
            f"(${float(record.cost_usd):.4f} USD, {record.input_tokens + record.output_tokens} tokens)"
        )

    @staticmethod
    def _make_json_safe(obj: Any) -> Any:
        """Recursively convert dates, Decimals, and other non-JSON types to primitives."""
        from datetime import date, datetime
        from decimal import Decimal
        if isinstance(obj, dict):
            return {k: StatementService._make_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [StatementService._make_json_safe(i) for i in obj]
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return obj

    # ------------------------------------------------------------------
    # Institution detection
    # ------------------------------------------------------------------

    async def _detect_institution(self, pdf_path: str, bank_name_hint: str = ""):
        """
        Detect the financial institution from PDF text by matching
        detection_keywords from the financial_institutions table.
        Returns FinancialInstitution ORM object or None.
        """
        try:
            import pdfplumber
            text = ""
            with pdfplumber.open(pdf_path) as pdf:
                if pdf.pages:
                    text = (pdf.pages[0].extract_text() or "").lower()
        except Exception:
            text = bank_name_hint.lower()

        result = await self.db.execute(select(FinancialInstitution))
        institutions = result.scalars().all()

        for inst in institutions:
            keywords = inst.detection_keywords or []
            if any(kw.lower() in text for kw in keywords):
                logger.info(f"Detected institution: {inst.name}")
                return inst

        logger.info(f"No institution match found, using hint: {bank_name_hint}")
        return None

    # ------------------------------------------------------------------
    # Database storage
    # ------------------------------------------------------------------

    async def _store_parsed_data(
        self,
        user_id: int,
        parsed_data: Dict[str, Any],
        filename: str,
        file_path: str,
        file_hash: str,
        password: Optional[str],
        bank_name: str,
        account_id: Optional[int] = None,
        extraction_method: str = "regex_fallback",
    ) -> Tuple[int, Dict[str, Any]]:
        """Store all parsed data in the database."""
        metadata = parsed_data["metadata"]
        transactions = parsed_data["transactions"]
        fees = parsed_data["fees"]
        interest_charges = parsed_data.get("interest_charges", [])
        rewards_data = parsed_data.get("rewards_data")

        self._validate_required_fields(metadata)

        # Use Claude-extracted bank_name if better than parameter default
        effective_bank_name = bank_name
        if metadata.get("bank_name") and metadata["bank_name"] not in ("Unknown", ""):
            effective_bank_name = metadata["bank_name"]

        # Detect card type from account number prefix
        card_type = self._detect_card_type(metadata.get("account_number", ""))

        # Calculate credit utilization if not already provided
        if not metadata.get("credit_utilization_pct"):
            new_bal = metadata.get("new_balance")
            credit_lim = metadata.get("credit_limit")
            if new_bal and credit_lim and credit_lim > 0:
                metadata["credit_utilization_pct"] = round(
                    (Decimal(str(new_bal)) / Decimal(str(credit_lim))) * 100, 2
                )

        _EXPLICIT = {"bank_name", "account_id", "extraction_method", "ai_confidence"}
        safe_meta = {k: v for k, v in metadata.items() if k not in _EXPLICIT}

        # Re-upload support: soft-delete old statement first
        existing = await self._check_duplicate_hash(file_hash)
        if not existing:
            existing = await self._check_duplicate_filename(filename)

        old_statement_id = None
        if existing:
            old_statement_id = existing.id
            logger.info(
                f"Re-upload detected in direct path (statement id={old_statement_id}), "
                "soft-deleting old data…"
            )
            existing.filename = f"__deleted__{existing.id}__{existing.filename}"
            existing.pdf_hash = f"__deleted__{existing.id}__{existing.pdf_hash}"
            await self.db.flush()

        statement = Statement(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            filename=filename,
            pdf_hash=file_hash,
            file_path=file_path,
            password=password,
            bank_name=effective_bank_name,
            card_type=card_type,
            account_id=account_id,
            extraction_method=extraction_method,
            ai_confidence=parsed_data.get("ai_confidence"),
            **safe_meta,
        )
        self.db.add(statement)
        await self.db.flush()

        transactions_added = 0
        successfully_added = []

        for txn_data in transactions:
            # Ensure billing_amount is populated for new transactions
            if not txn_data.get("billing_amount") and txn_data.get("amount"):
                txn_data["billing_amount"] = txn_data["amount"]
            if not txn_data.get("billing_currency") and txn_data.get("currency"):
                txn_data["billing_currency"] = txn_data["currency"]

            # Use per-transaction account_id (set by DataNormalizer for multi-card
            # statements), falling back to the statement-level account_id.
            txn_account_id = txn_data.get("account_id") or account_id
            txn_account_number = txn_data.get("account_number") or statement.account_number

            txn_fields = {k: v for k, v in txn_data.items() if k not in ("account_number", "statement_id", "account_id")}

            txn = Transaction(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                statement_id=statement.id,
                account_number=txn_account_number,
                account_id=txn_account_id,
                **txn_fields,
            )
            self.db.add(txn)
            transactions_added += 1
            successfully_added.append(txn_data)

        await self.db.flush()

        fees_added = 0
        for fee_data in fees:
            fee = Fee(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                statement_id=statement.id,
                account_number=statement.account_number,
                fee_date=statement.statement_date,
                **fee_data,
            )
            self.db.add(fee)
            fees_added += 1

        for interest_data in interest_charges:
            interest = InterestCharge(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                statement_id=statement.id,
                account_number=statement.account_number,
                **interest_data,
            )
            self.db.add(interest)

        await self._store_category_summaries(user_id, statement, successfully_added)

        # Store rewards summary
        if rewards_data:
            rewards = RewardsSummary(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                statement_id=statement.id,
                account_number=statement.account_number,
                statement_date=statement.statement_date,
                **rewards_data,
            )
            self.db.add(rewards)
        elif metadata.get("rewards_opening") is not None:
            rewards = RewardsSummary(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                statement_id=statement.id,
                account_number=statement.account_number,
                statement_date=statement.statement_date,
                opening_balance=metadata.get("rewards_opening", 0),
                earned_purchases=metadata.get("rewards_earned", 0),
                earned_bonus=0,
                earned_welcome=0,
                redeemed_travel=0,
                redeemed_cashback=0,
                redeemed_vouchers=0,
                redeemed_other=metadata.get("rewards_redeemed", 0),
                expired=0,
                adjusted=0,
                closing_balance=metadata.get("rewards_closing", 0),
                points_value_inr=metadata.get("rewards_value_inr"),
            )
            self.db.add(rewards)

        await self.db.commit()

        # Permanently delete the old (soft-deleted) statement now that new data is committed
        if old_statement_id:
            try:
                old_stmt = await self.db.execute(
                    select(Statement).where(Statement.id == old_statement_id)
                )
                old_stmt_obj = old_stmt.scalar_one_or_none()
                if old_stmt_obj:
                    await self._delete_statement_cascade(old_stmt_obj)
                    await self.db.commit()
                    logger.info(f"Permanently deleted old statement id={old_statement_id} (direct path)")
            except Exception as e:
                logger.warning(f"Failed to clean up old statement id={old_statement_id}: {e}")

        stats = {
            "transactions_added": transactions_added,
            "transactions_skipped": 0,
            "fees_added": fees_added,
            "statement_date": statement.statement_date.isoformat() if statement.statement_date else None,
            "total_amount": float(statement.total_amount_due) if statement.total_amount_due else None,
        }
        return statement.id, stats

    async def _store_ai_extraction(
        self, user_id: int, statement_id: int, data: Dict[str, Any], file_hash: Optional[str] = None
    ):
        """
        Link the statement to its AiExtraction cache record (if one exists),
        or create a new lightweight audit row if the cache wasn't used.
        """
        from decimal import Decimal

        if file_hash:
            # Update the existing cache record to point at this statement
            result = await self.db.execute(
                select(AiExtraction)
                .where(
                    AiExtraction.file_hash == file_hash,
                    AiExtraction.raw_response.isnot(None),
                )
                .order_by(AiExtraction.created_at.desc())
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.statement_id = statement_id
                await self.db.commit()
                return

        # No cache record — create a new audit row (e.g. direct upload path)
        ai_ext = AiExtraction(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            statement_id=statement_id,
            file_hash=file_hash,
            model_used=data.get("model_used", "claude-sonnet-4-5"),
            pages_processed=data.get("pages_processed", 0),
            pages_skipped=data.get("pages_skipped", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cost_usd=Decimal(str(data.get("cost_usd", 0))),
            extraction_confidence=Decimal(str(data.get("extraction_confidence", 1.0))),
            issues_flagged=data.get("issues_flagged", []),
        )
        self.db.add(ai_ext)
        await self.db.commit()

    async def _delete_statement_cascade(self, stmt: Statement):
        """Delete a statement and all its children using explicit bulk deletes."""
        sid = stmt.id
        for model in (AiExtraction, CategorySummary, RewardsSummary, Payment,
                      InterestCharge, Fee, Transaction):
            await self.db.execute(sa_delete(model).where(model.statement_id == sid))
        await self.db.delete(stmt)
        await self.db.flush()

    async def _store_category_summaries(self, user_id: int, statement: Statement, transactions: List[Dict]):
        """Calculate and store category summaries."""
        from app.services.category_engine import CategoryEngine

        # Clear any existing summaries for this statement (safe for re-uploads)
        await self.db.execute(
            sa_delete(CategorySummary).where(CategorySummary.statement_id == statement.id)
        )

        category_data: Dict[str, Any] = {}

        for txn in transactions:
            # Determine the best category to use — user override takes priority
            source = txn.get("category_source", "")
            if source == "user_override" and txn.get("category_manual"):
                category = txn["category_manual"]
            else:
                category = (
                    txn.get("category_ai")
                    or txn.get("merchant_category")
                    or txn.get("category_manual")
                    or "Other"
                )
            amount = float(txn.get("billing_amount") or txn.get("amount") or 0)

            if txn.get("debit_credit") == "D":
                if category not in category_data:
                    category_data[category] = {"count": 0, "total": Decimal(0), "rewards": 0}
                category_data[category]["count"] += 1
                category_data[category]["total"] += Decimal(str(amount))
                category_data[category]["rewards"] += txn.get("rewards_earned", 0)

        total_spending = sum(d["total"] for d in category_data.values())

        for category_name, data in category_data.items():
            pct = (data["total"] / total_spending * 100) if total_spending > 0 else 0
            avg = data["total"] / data["count"] if data["count"] > 0 else 0

            category_summary = CategorySummary(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                statement_id=statement.id,
                account_number=statement.account_number,
                category_name=category_name,
                transaction_count=data["count"],
                total_amount=data["total"],
                percentage_of_spending=round(pct, 2),
                avg_transaction_amount=round(avg, 2),
                rewards_earned=data["rewards"],
                currency=statement.currency or settings.default_currency,
            )
            self.db.add(category_summary)

        await self.db.flush()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_required_fields(self, metadata: Dict[str, Any]) -> None:
        required = {
            "statement_date": "Statement Date",
            "statement_period_from": "Statement Period From",
            "statement_period_to": "Statement Period To",
        }
        missing = [label for field, label in required.items() if not metadata.get(field)]
        if missing:
            raise ValueError(
                f"Missing required fields: {', '.join(missing)}\n"
                "These dates must be present in the PDF."
            )

    async def _check_duplicate_filename(self, filename: str, user_id: int) -> Optional[Statement]:
        """Find a statement by filename, including soft-deleted variants."""
        result = await self.db.execute(
            select(Statement).where(
                or_(
                    Statement.filename == filename,
                    Statement.filename.like(f"%__{filename}"),
                ),
                Statement.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def _check_duplicate_hash(self, file_hash: str, user_id: int) -> Optional[Statement]:
        """Find a statement by pdf hash, including soft-deleted variants."""
        result = await self.db.execute(
            select(Statement).where(
                or_(
                    Statement.pdf_hash == file_hash,
                    Statement.pdf_hash.like(f"%__{file_hash}"),
                ),
                Statement.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Read methods (unchanged)
    # ------------------------------------------------------------------

    async def get_statement(self, statement_id: int, user_id: int) -> Optional[Statement]:
        result = await self.db.execute(
            select(Statement).where(
                Statement.id == statement_id,
                Statement.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_all_statements(self, user_id: int, limit: int = 100, offset: int = 0) -> List[Statement]:
        result = await self.db.execute(
            select(Statement)
            .where(Statement.user_id == user_id)
            .order_by(Statement.statement_date.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_transactions(
        self,
        statement_id: int,
        user_id: int,
        category: Optional[str] = None,
        merchant: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Transaction]:
        query = select(Transaction).where(
            Transaction.statement_id == statement_id,
            Transaction.user_id == user_id
        )
        if category:
            query = query.where(Transaction.merchant_category == category)
        if merchant:
            query = query.where(Transaction.merchant_name.ilike(f"%{merchant}%"))
        query = query.order_by(Transaction.transaction_date.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_analytics(self, statement_id: int, user_id: int) -> Dict[str, Any]:
        statement = await self.get_statement(statement_id, user_id)
        if not statement:
            return {}
        result = await self.db.execute(
            select(CategorySummary)
            .where(
                CategorySummary.statement_id == statement_id,
                CategorySummary.user_id == user_id,
            )
            .order_by(CategorySummary.total_amount.desc())
        )
        categories = list(result.scalars().all())
        result = await self.db.execute(
            select(Transaction).where(
                Transaction.statement_id == statement_id,
                Transaction.user_id == user_id,
            )
        )
        transactions = list(result.scalars().all())
        return {
            "statement": statement,
            "categories": categories,
            "transaction_count": len(transactions),
            "total_spending": sum(c.total_amount for c in categories),
        }
