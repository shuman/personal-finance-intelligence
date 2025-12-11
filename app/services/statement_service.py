"""
Statement processing service.
Handles complete workflow from PDF upload to database storage.
"""
import os
import hashlib
import shutil
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import (
    Statement, Transaction, Fee, InterestCharge,
    RewardsSummary, CategorySummary, Payment
)
from app.parsers import ParserFactory, AmexParser
from app.config import settings
from app.utils.categorization import calculate_category_summary


class StatementService:
    """
    Service for processing credit card statement PDFs.

    Workflow:
    1. Save uploaded PDF file
    2. Check for duplicates (filename, file hash)
    3. Parse PDF with appropriate bank parser
    4. Store statement metadata
    5. Store all transactions (with duplicate detection)
    6. Store fees, interest, rewards
    7. Calculate and store category summaries
    8. Return processing summary
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.upload_dir = settings.upload_dir

        # Ensure upload directory exists
        os.makedirs(self.upload_dir, exist_ok=True)

    async def process_statement(
        self,
        file_content: bytes,
        filename: str,
        password: Optional[str] = None,
        bank_name: str = "Amex"
    ) -> Dict[str, Any]:
        """
        Main method to process uploaded statement PDF.

        Args:
            file_content: PDF file content bytes
            filename: Original filename
            password: PDF password if protected
            bank_name: Bank name (default: "Amex")

        Returns:
            Processing summary with statement ID and statistics

        Raises:
            ValueError: If PDF cannot be parsed or duplicate found
        """
        # Calculate file hash for duplicate detection
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Check for duplicate by filename
        existing_stmt = await self._check_duplicate_filename(filename)
        if existing_stmt:
            raise ValueError(f"Statement with filename '{filename}' already exists (ID: {existing_stmt.id})")

        # Check for duplicate by hash
        existing_stmt = await self._check_duplicate_hash(file_hash)
        if existing_stmt:
            raise ValueError(f"Identical statement file already uploaded (ID: {existing_stmt.id})")

        # Save file to disk
        file_path = os.path.join(self.upload_dir, filename)
        with open(file_path, 'wb') as f:
            f.write(file_content)

        try:
            # Decrypt PDF if password protected
            working_file = file_path
            if password:
                parser = AmexParser()
                try:
                    working_file = parser.decrypt_pdf(file_path, password)
                except Exception as e:
                    raise ValueError(f"Failed to decrypt PDF: {e}")

            # Get appropriate parser
            parser = ParserFactory.get_parser(working_file, bank_name)

            # Parse PDF
            parsed_data = parser.parse(working_file)

            # Store in database
            statement_id, stats = await self._store_parsed_data(
                parsed_data,
                filename,
                file_path,
                file_hash,
                password,
                bank_name
            )

            return {
                "statement_id": statement_id,
                "filename": filename,
                "bank_name": bank_name,
                "transactions_added": stats["transactions_added"],
                "transactions_skipped": stats["transactions_skipped"],
                "fees_added": stats["fees_added"],
                "statement_date": stats["statement_date"],
                "total_amount": stats["total_amount"],
                "message": "Statement processed successfully"
            }

        except Exception as e:
            # Clean up file on error
            if os.path.exists(file_path):
                os.remove(file_path)
            raise ValueError(f"Error processing statement: {str(e)}")

    async def _check_duplicate_filename(self, filename: str) -> Optional[Statement]:
        """Check if statement with this filename already exists."""
        result = await self.db.execute(
            select(Statement).where(Statement.filename == filename)
        )
        return result.scalar_one_or_none()

    async def _check_duplicate_hash(self, file_hash: str) -> Optional[Statement]:
        """Check if statement with this hash already exists."""
        result = await self.db.execute(
            select(Statement).where(Statement.pdf_hash == file_hash)
        )
        return result.scalar_one_or_none()

    def _validate_required_fields(self, metadata: Dict[str, Any]) -> None:
        """
        Validate that all required (NOT NULL) fields are present.

        Raises:
            ValueError: With detailed message showing which fields are missing
        """
        required_fields = {
            "statement_date": "Statement Date",
            "statement_period_from": "Statement Period From",
            "statement_period_to": "Statement Period To",
        }

        missing_fields = []
        for field, label in required_fields.items():
            if metadata.get(field) is None:
                missing_fields.append(f"{label} ({field})")

        if missing_fields:
            fields_list = "\n  - " + "\n  - ".join(missing_fields)
            raise ValueError(
                f"Missing required fields in statement data:{fields_list}\n\n"
                f"These fields are required by the database and must be extracted from the PDF. "
                f"Please check if the PDF format matches the expected format or if these dates are present in the PDF."
            )

    async def _store_parsed_data(
        self,
        parsed_data: Dict[str, Any],
        filename: str,
        file_path: str,
        file_hash: str,
        password: Optional[str],
        bank_name: str
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Store all parsed data in database.

        Returns:
            Tuple of (statement_id, statistics)
        """
        metadata = parsed_data["metadata"]
        transactions = parsed_data["transactions"]
        fees = parsed_data["fees"]
        interest_charges = parsed_data["interest_charges"]

        # Validate required fields before creating statement
        self._validate_required_fields(metadata)

        # Create statement record
        statement = Statement(
            filename=filename,
            pdf_hash=file_hash,
            file_path=file_path,
            password=password,
            bank_name=bank_name,
            **metadata
        )

        self.db.add(statement)
        await self.db.flush()  # Get statement ID

        # Store transactions - SIMPLE: Just add them all
        transactions_added = 0
        successfully_added_transactions = []

        for txn_data in transactions:
            transaction = Transaction(
                statement_id=statement.id,
                account_number=statement.account_number,
                **txn_data
            )
            self.db.add(transaction)
            transactions_added += 1
            successfully_added_transactions.append(txn_data)

        # Flush transactions - if error, let it fail completely
        await self.db.flush()

        # Store fees
        fees_added = 0
        for fee_data in fees:
            fee = Fee(
                statement_id=statement.id,
                account_number=statement.account_number,
                fee_date=statement.statement_date,
                **fee_data
            )
            self.db.add(fee)
            fees_added += 1

        # Store interest charges
        for interest_data in interest_charges:
            interest = InterestCharge(
                statement_id=statement.id,
                account_number=statement.account_number,
                **interest_data
            )
            self.db.add(interest)

        # Calculate and store category summaries
        await self._store_category_summaries(statement, successfully_added_transactions)

        # Store rewards summary if data available
        if metadata.get("rewards_opening") is not None:
            rewards = RewardsSummary(
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

        # FINAL COMMIT - everything or nothing
        await self.db.commit()

        stats = {
            "transactions_added": transactions_added,
            "transactions_skipped": 0,  # No duplicate tracking anymore
            "fees_added": fees_added,
            "statement_date": statement.statement_date.isoformat() if statement.statement_date else None,
            "total_amount": float(statement.total_amount_due) if statement.total_amount_due else None,
        }

        return statement.id, stats

    async def _store_category_summaries(self, statement: Statement, transactions: List[Dict]):
        """Calculate and store category summaries."""
        # Group transactions by category
        category_data = {}

        for txn in transactions:
            category = txn.get("merchant_category", "Other")
            amount = float(txn.get("amount", 0))

            # Only count debits for spending
            if txn.get("debit_credit") == "D":
                if category not in category_data:
                    category_data[category] = {
                        "count": 0,
                        "total": Decimal(0),
                        "rewards": 0
                    }

                category_data[category]["count"] += 1
                category_data[category]["total"] += Decimal(str(amount))
                category_data[category]["rewards"] += txn.get("rewards_earned", 0)

        # Calculate total spending for percentages
        total_spending = sum(data["total"] for data in category_data.values())

        # Create category summary records
        for category_name, data in category_data.items():
            percentage = (data["total"] / total_spending * 100) if total_spending > 0 else 0
            avg_amount = data["total"] / data["count"] if data["count"] > 0 else 0

            category_summary = CategorySummary(
                statement_id=statement.id,
                account_number=statement.account_number,
                category_name=category_name,
                transaction_count=data["count"],
                total_amount=data["total"],
                percentage_of_spending=round(percentage, 2),
                avg_transaction_amount=round(avg_amount, 2),
                rewards_earned=data["rewards"],
                currency="INR"
            )
            self.db.add(category_summary)

        # Flush all category summaries at once - let errors propagate up
        await self.db.flush()

    async def get_statement(self, statement_id: int) -> Optional[Statement]:
        """Get statement by ID."""
        result = await self.db.execute(
            select(Statement).where(Statement.id == statement_id)
        )
        return result.scalar_one_or_none()

    async def get_all_statements(self, limit: int = 100, offset: int = 0) -> List[Statement]:
        """Get all statements with pagination."""
        result = await self.db.execute(
            select(Statement)
            .order_by(Statement.statement_date.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_transactions(
        self,
        statement_id: int,
        category: Optional[str] = None,
        merchant: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Transaction]:
        """Get transactions for a statement with filters."""
        query = select(Transaction).where(Transaction.statement_id == statement_id)

        if category:
            query = query.where(Transaction.merchant_category == category)

        if merchant:
            query = query.where(Transaction.merchant_name.ilike(f"%{merchant}%"))

        query = query.order_by(Transaction.transaction_date.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_analytics(self, statement_id: int) -> Dict[str, Any]:
        """Get analytics for a statement."""
        statement = await self.get_statement(statement_id)
        if not statement:
            return {}

        # Get category summaries
        result = await self.db.execute(
            select(CategorySummary)
            .where(CategorySummary.statement_id == statement_id)
            .order_by(CategorySummary.total_amount.desc())
        )
        categories = list(result.scalars().all())

        # Get transaction count
        result = await self.db.execute(
            select(Transaction).where(Transaction.statement_id == statement_id)
        )
        transactions = list(result.scalars().all())

        return {
            "statement": statement,
            "categories": categories,
            "transaction_count": len(transactions),
            "total_spending": sum(c.total_amount for c in categories),
        }

    async def save_previewed_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Save previewed and edited statement data to database.

        Args:
            data: Dictionary containing edited metadata, transactions, etc.

        Returns:
            Processing summary
        """
        from datetime import date, datetime as dt

        filename = data["filename"]
        file_hash = data["file_hash"]
        password = data.get("password")
        bank_name = data["bank_name"]
        metadata = data["metadata"]
        transactions = data["transactions"]
        fees = data.get("fees", [])

        # Check for duplicates
        existing_stmt = await self._check_duplicate_filename(filename)
        if existing_stmt:
            raise ValueError(f"Statement with filename '{filename}' already exists")

        existing_stmt = await self._check_duplicate_hash(file_hash)
        if existing_stmt:
            raise ValueError(f"Identical statement file already uploaded")

        # Move temp file to permanent location
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
                except:
                    metadata[key] = None

        # Convert numeric strings to Decimal
        numeric_fields = [
            "previous_balance", "payments_credits", "purchases", "cash_advances",
            "fees_charged", "interest_charged", "adjustments", "new_balance",
            "total_amount_due", "minimum_payment_due", "credit_limit",
            "available_credit", "cash_advance_limit", "credit_utilization_pct",
            "rewards_value_inr"
        ]
        for field in numeric_fields:
            if metadata.get(field) is not None:
                try:
                    metadata[field] = Decimal(str(metadata[field]))
                except:
                    metadata[field] = None

        # Validate required fields before creating statement
        self._validate_required_fields(metadata)

        # Create statement
        statement = Statement(
            filename=filename,
            pdf_hash=file_hash,
            file_path=file_path,
            password=password,
            bank_name=bank_name,
            **metadata
        )

        self.db.add(statement)
        await self.db.flush()

        # Store transactions
        transactions_added = 0
        transactions_skipped = 0
        duplicate_transactions = []
        successfully_added_transactions = []  # Track transactions that were actually saved

        # Convert all transaction data first
        for txn_data in transactions:
            if txn_data.get("transaction_date"):
                txn_data["transaction_date"] = dt.fromisoformat(txn_data["transaction_date"]).date()
            if txn_data.get("posting_date"):
                txn_data["posting_date"] = dt.fromisoformat(txn_data["posting_date"]).date()
            if txn_data.get("amount"):
                txn_data["amount"] = Decimal(str(txn_data["amount"]))
            if txn_data.get("foreign_amount"):
                txn_data["foreign_amount"] = Decimal(str(txn_data["foreign_amount"]))
            if txn_data.get("exchange_rate"):
                txn_data["exchange_rate"] = Decimal(str(txn_data["exchange_rate"]))

        # Try to add all transactions at once
        for txn_data in transactions:
            transaction = Transaction(
                statement_id=statement.id,
                account_number=statement.account_number,
                **txn_data
            )
            self.db.add(transaction)
            transactions_added += 1
            successfully_added_transactions.append(txn_data)

        try:
            await self.db.flush()
        except IntegrityError:
            # Duplicates found, add one by one
            await self.db.rollback()
            transactions_added = 0
            successfully_added_transactions = []

            # Re-add statement
            statement = Statement(
                filename=filename,
                pdf_hash=file_hash,
                file_path=file_path,
                password=password,
                bank_name=bank_name,
                **metadata
            )
            self.db.add(statement)
            await self.db.flush()

            for idx, txn_data in enumerate(transactions, 1):
                try:
                    transaction = Transaction(
                        statement_id=statement.id,
                        account_number=statement.account_number,
                        **txn_data
                    )
                    self.db.add(transaction)
                    await self.db.flush()
                    transactions_added += 1
                    successfully_added_transactions.append(txn_data)
                except IntegrityError:
                    transactions_skipped += 1
                    txn_date = txn_data.get("transaction_date")
                    txn_amount = txn_data.get("amount")
                    duplicate_transactions.append({
                        "index": idx,
                        "date": txn_date.isoformat() if hasattr(txn_date, 'isoformat') else str(txn_date),
                        "description": str(txn_data.get("description_raw", "Unknown"))[:50],
                        "amount": float(txn_amount) if txn_amount is not None else None
                    })
                    await self.db.rollback()
                    await self.db.flush()

        # Store fees
        for fee_data in fees:
            if fee_data.get("amount"):
                fee_data["amount"] = Decimal(str(fee_data["amount"]))

            fee = Fee(
                statement_id=statement.id,
                account_number=statement.account_number,
                fee_date=statement.statement_date,
                **fee_data
            )
            self.db.add(fee)

        # Check if all transactions were duplicates BEFORE storing summaries
        if transactions_skipped > 0 and transactions_added == 0:
            await self.db.rollback()
            raise ValueError(
                f"All {len(transactions)} transactions are duplicates. "
                f"This file may have already been uploaded. "
                f"Check the statement list for existing statements from the same period."
            )

        # Calculate and store category summaries (only from successfully added transactions)
        # This must be done BEFORE commit
        try:
            await self._store_category_summaries(statement, successfully_added_transactions)
        except Exception as e:
            # If category summaries fail, rollback everything
            await self.db.rollback()
            raise ValueError(f"Error creating category summaries: {str(e)}")

        # FINAL COMMIT - everything or nothing
        await self.db.commit()

        result = {
            "statement_id": statement.id,
            "filename": filename,
            "bank_name": bank_name,
            "transactions_added": transactions_added,
            "transactions_skipped": transactions_skipped,
            "statement_date": statement.statement_date.isoformat() if statement.statement_date else None,
            "total_amount": float(statement.total_amount_due) if statement.total_amount_due else None,
            "message": "Statement saved successfully"
        }

        # Add warning if duplicates were found
        if transactions_skipped > 0:
            result["warning"] = (
                f"{transactions_skipped} duplicate transaction(s) were skipped. "
                f"These transactions had identical date, description, and amount."
            )
            if duplicate_transactions:
                result["duplicate_details"] = duplicate_transactions

        return result
