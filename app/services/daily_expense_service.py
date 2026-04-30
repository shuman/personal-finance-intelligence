"""
Daily Expense Service - Manual cash expense logging with batch AI categorization.

Workflow:
  1. User saves draft expense (instant, no AI call)
  2. User selects multiple drafts for processing
  3. Batch process: Send all to Claude Haiku in one request
  4. AI enriches: category, subcategory, tags, normalized description
  5. User reviews and can override AI suggestions
  6. Overrides saved as category rules for future matching
"""
import json
import logging
import uuid
from datetime import datetime, date, time

from app.services.categories import UNIFIED_CATEGORIES
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy import select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyExpense, CategoryRule

logger = logging.getLogger(__name__)


class DailyExpenseService:
    """Service for managing daily expense transactions with batch AI processing."""

    # Standard expense categories — single source of truth
    CATEGORIES = UNIFIED_CATEGORIES

    # Payment methods
    PAYMENT_METHODS = ["cash", "bkash", "nagad", "rocket", "card_estimate"]

    def __init__(self, db: AsyncSession):
        self.db = db
        self._claude_client = None

    def _get_claude_client(self):
        """Get or create Anthropic client."""
        if self._claude_client is None and settings.anthropic_api_key:
            import anthropic
            self._claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._claude_client

    # -----------------------------------------------------------------------
    # CRUD Operations
    # -----------------------------------------------------------------------

    async def save_draft_expense(
        self,
        user_id: int,
        amount: Decimal,
        description: str,
        transaction_date: Optional[date] = None,
        payment_method: str = "cash",
        currency: str = "BDT",
    ) -> DailyExpense:
        """
        Save a new draft expense (no AI processing yet).
        Returns the created expense object.
        """
        if transaction_date is None:
            transaction_date = date.today()

        if payment_method not in self.PAYMENT_METHODS:
            payment_method = "cash"

        expense = DailyExpense(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            amount=amount,
            currency=currency,
            description_raw=description.strip(),
            payment_method=payment_method,
            transaction_date=transaction_date,
            transaction_time=datetime.now().time(),
            ai_status="draft",
            created_at=datetime.utcnow(),
        )

        self.db.add(expense)
        await self.db.commit()
        await self.db.refresh(expense)

        logger.info(f"Saved draft expense: {expense.id} - {amount} BDT - {description[:30]}")
        return expense

    async def get_expenses(
        self,
        user_id: int,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        needs_review: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DailyExpense]:
        """
        Get expenses with optional filters.

        Args:
            user_id: Filter by user ownership
            status: Filter by ai_status (draft, pending, processed)
            date_from: Filter by transaction_date >= date_from
            date_to: Filter by transaction_date <= date_to
            needs_review: Filter by needs_review flag
            limit: Maximum number of results
            offset: Offset for pagination
        """
        query = select(DailyExpense).where(DailyExpense.user_id == user_id)

        # Build filters
        filters = []
        if status:
            filters.append(DailyExpense.ai_status == status)
        if date_from:
            filters.append(DailyExpense.transaction_date >= date_from)
        if date_to:
            filters.append(DailyExpense.transaction_date <= date_to)
        if needs_review is not None:
            filters.append(DailyExpense.needs_review == needs_review)

        if filters:
            query = query.where(and_(*filters))

        # Order by most recent first
        query = query.order_by(desc(DailyExpense.created_at))
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_expense_by_id(self, expense_id: int, user_id: Optional[int] = None) -> Optional[DailyExpense]:
        """Get a single expense by ID (optionally scoped to user)."""
        query = select(DailyExpense).where(DailyExpense.id == expense_id)
        if user_id is not None:
            query = query.where(DailyExpense.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def delete_expense(self, expense_id: int, user_id: int) -> bool:
        """Delete an expense."""
        expense = await self.get_expense_by_id(expense_id, user_id=user_id)
        if not expense:
            return False

        await self.db.delete(expense)
        await self.db.commit()
        logger.info(f"Deleted expense: {expense_id}")
        return True

    # -----------------------------------------------------------------------
    # Batch AI Processing
    # -----------------------------------------------------------------------

    async def mark_for_processing(self, expense_ids: List[int], user_id: int) -> int:
        """
        Mark selected expenses as 'pending' for batch processing.
        Returns count of expenses marked.
        """
        result = await self.db.execute(
            select(DailyExpense).where(
                and_(
                    DailyExpense.id.in_(expense_ids),
                    DailyExpense.ai_status == "draft",
                    DailyExpense.user_id == user_id,
                )
            )
        )
        expenses = result.scalars().all()

        for expense in expenses:
            expense.ai_status = "pending"

        await self.db.commit()
        logger.info(f"Marked {len(expenses)} expenses as pending")
        return len(expenses)

    async def batch_categorize_expenses(self, expense_ids: List[int], user_id: int) -> Dict[str, Any]:
        """
        Batch process expenses with Claude AI.
        Sends all expenses in one API call for cost efficiency.

        Returns:
            {
                "success_count": int,
                "failed_count": int,
                "total_cost_usd": float,
                "expenses_processed": [expense_id, ...]
            }
        """
        # Get pending expenses
        result = await self.db.execute(
            select(DailyExpense).where(
                and_(
                    DailyExpense.id.in_(expense_ids),
                    DailyExpense.ai_status == "pending",
                    DailyExpense.user_id == user_id,
                )
            )
        )
        expenses = list(result.scalars().all())

        if not expenses:
            logger.warning("No pending expenses found for batch processing")
            return {
                "success_count": 0,
                "failed_count": 0,
                "total_cost_usd": 0.0,
                "expenses_processed": [],
            }

        # Try Claude AI batch categorization
        if not settings.anthropic_api_key:
            logger.error("No Anthropic API key configured")
            return {
                "success_count": 0,
                "failed_count": len(expenses),
                "total_cost_usd": 0.0,
                "expenses_processed": [],
            }

        try:
            results = await self._batch_categorize_with_claude(expenses)

            success_count = 0
            failed_count = 0

            for expense_id, result_data in results.items():
                expense = next((e for e in expenses if e.id == expense_id), None)
                if not expense:
                    continue

                if result_data.get("error"):
                    expense.ai_status = "draft"  # Reset to draft on failure
                    expense.needs_review = True
                    failed_count += 1
                else:
                    # Update expense with AI results
                    expense.category = result_data.get("category")
                    expense.subcategory = result_data.get("subcategory")
                    expense.description_normalized = result_data.get("description_normalized")
                    expense.tags = result_data.get("tags", [])
                    expense.confidence_score = Decimal(str(result_data.get("confidence", 0.8)))
                    expense.needs_review = expense.confidence_score < Decimal("0.7")
                    expense.ai_status = "processed"
                    expense.enriched_at = datetime.utcnow()
                    success_count += 1

                    # Store as category rule for future matching
                    await self._store_category_rule(
                        user_id=user_id,
                        description=expense.description_raw,
                        category=expense.category,
                        subcategory=expense.subcategory,
                        confidence=float(expense.confidence_score),
                    )

            await self.db.commit()

            return {
                "success_count": success_count,
                "failed_count": failed_count,
                "total_cost_usd": results.get("_cost_usd", 0.0),
                "expenses_processed": [e.id for e in expenses if e.ai_status == "processed"],
            }

        except Exception as e:
            logger.error(f"Batch categorization failed: {e}")
            # Reset expenses to draft on failure
            for expense in expenses:
                expense.ai_status = "draft"
            await self.db.commit()

            return {
                "success_count": 0,
                "failed_count": len(expenses),
                "total_cost_usd": 0.0,
                "expenses_processed": [],
            }

    async def _batch_categorize_with_claude(
        self, expenses: List[DailyExpense]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Send all expenses to Claude in a single API call.
        Returns: {expense_id: {category, subcategory, description_normalized, tags, confidence}}
        """
        client = self._get_claude_client()
        if not client:
            raise ValueError("No Anthropic client available")

        # Build batch prompt
        categories_str = ", ".join(self.CATEGORIES)

        expense_lines = []
        for i, expense in enumerate(expenses, 1):
            expense_lines.append(
                f"{i}. ID: {expense.id}, Amount: {expense.amount} {expense.currency}, "
                f"Description: \"{expense.description_raw}\", Date: {expense.transaction_date}"
            )

        expenses_text = "\n".join(expense_lines)

        prompt = f"""You are categorizing daily cash expenses for a user in Bangladesh.

Available categories: {categories_str}

Expenses to categorize (multilingual: Bangla, English, Banglish accepted):
{expenses_text}

For EACH expense, provide:
1. category (from the list above)
2. subcategory (2-4 words, specific)
3. description_normalized (clean, proper English description)
4. tags (array of 2-4 relevant keywords)
5. confidence (0.0-1.0, how confident you are)

Return ONLY valid JSON array, one object per expense in the SAME ORDER:
[
  {{
    "expense_id": {expenses[0].id},
    "category": "...",
    "subcategory": "...",
    "description_normalized": "...",
    "tags": ["tag1", "tag2"],
    "confidence": 0.85
  }},
  ...
]

Rules:
- If description is multilingual (Bangla/Banglish), translate to English in description_normalized
- Choose specific subcategories (e.g., "Street Food" not just "Food")
- Use contextual hints: "chaa/tea" = Beverages, "rickshaw/cng" = Transport, etc.
- If uncertain, lower confidence score
"""

        # Call Claude API
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        # Calculate cost (Claude Haiku: $0.80/M input, $4.00/M output)
        cost_usd = (input_tokens / 1_000_000 * 0.80) + (output_tokens / 1_000_000 * 4.00)

        # Parse response
        raw = response.content[0].text.strip()

        # Strip markdown code blocks if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if raw.endswith("```") else lines[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])

        try:
            results_array = json.loads(raw.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response: {e}\nRaw: {raw}")
            raise

        # Convert array to dict keyed by expense_id
        results = {}
        for item in results_array:
            expense_id = item.get("expense_id")
            if expense_id:
                results[expense_id] = {
                    "category": item.get("category", "Other"),
                    "subcategory": item.get("subcategory"),
                    "description_normalized": item.get("description_normalized"),
                    "tags": item.get("tags", []),
                    "confidence": item.get("confidence", 0.8),
                }

        # Handle any expenses that weren't categorized
        for expense in expenses:
            if expense.id not in results:
                results[expense.id] = {"error": "Not returned by AI"}

        results["_cost_usd"] = cost_usd
        logger.info(
            f"Batch categorized {len(expenses)} expenses. "
            f"Cost: ${cost_usd:.6f} ({input_tokens} in + {output_tokens} out tokens)"
        )

        return results

    async def _store_category_rule(
        self,
        user_id: int,
        description: str,
        category: str,
        subcategory: Optional[str],
        confidence: float,
    ):
        """Store AI categorization as a rule for future matching."""
        normalized = self._normalize_description(description)

        # Check if rule already exists
        result = await self.db.execute(
            select(CategoryRule).where(
                CategoryRule.normalized_merchant == normalized,
                CategoryRule.is_active == True,
                CategoryRule.user_id == user_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update if new confidence is higher
            if confidence > float(existing.confidence):
                existing.category = category
                existing.subcategory = subcategory
                existing.confidence = Decimal(str(confidence))
                existing.match_count += 1
                existing.last_matched_at = datetime.utcnow()
                existing.updated_at = datetime.utcnow()
        else:
            # Create new rule
            rule = CategoryRule(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                merchant_pattern=description[:200],
                normalized_merchant=normalized,
                category=category,
                subcategory=subcategory,
                source="claude_ai",
                confidence=Decimal(str(confidence)),
                match_count=1,
                last_matched_at=datetime.utcnow(),
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(rule)

    # -----------------------------------------------------------------------
    # Basic Field Updates
    # -----------------------------------------------------------------------

    async def update_basic_fields(
        self,
        expense_id: int,
        user_id: int,
        fields: dict,
    ) -> Optional[DailyExpense]:
        """Update basic editable fields (amount, description_raw, etc.) on an expense."""
        expense = await self.get_expense_by_id(expense_id, user_id=user_id)
        if not expense:
            return None

        if fields.get("amount") is not None:
            expense.amount = Decimal(str(fields["amount"]))
        if fields.get("description_raw") is not None:
            expense.description_raw = fields["description_raw"]
        if fields.get("payment_method") is not None:
            expense.payment_method = fields["payment_method"]
        if fields.get("transaction_date") is not None:
            expense.transaction_date = fields["transaction_date"]
        if fields.get("currency") is not None:
            expense.currency = fields["currency"]

        await self.db.commit()
        await self.db.refresh(expense)
        return expense

    # -----------------------------------------------------------------------
    # User Overrides
    # -----------------------------------------------------------------------

    async def apply_user_override(
        self,
        expense_id: int,
        user_id: int,
        category: str,
        subcategory: Optional[str] = None,
        description_normalized: Optional[str] = None,
    ) -> Optional[DailyExpense]:
        """
        Apply user corrections to an expense and store as high-priority rule.
        """
        expense = await self.get_expense_by_id(expense_id, user_id=user_id)
        if not expense:
            return None

        # Update expense
        expense.category = category
        expense.subcategory = subcategory
        if description_normalized:
            expense.description_normalized = description_normalized
        expense.confidence_score = Decimal("1.0")  # User override = 100% confidence
        expense.needs_review = False
        expense.ai_status = "processed"

        # Store as user_override rule (highest priority)
        normalized = self._normalize_description(expense.description_raw)

        # Upsert user override rule
        result = await self.db.execute(
            select(CategoryRule).where(
                CategoryRule.normalized_merchant == normalized,
                CategoryRule.source == "user_override",
                CategoryRule.user_id == user_id,
            )
        )
        rule = result.scalar_one_or_none()

        if rule:
            rule.category = category
            rule.subcategory = subcategory
            rule.confidence = Decimal("1.0")
            rule.match_count += 1
            rule.last_matched_at = datetime.utcnow()
            rule.updated_at = datetime.utcnow()
        else:
            rule = CategoryRule(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                merchant_pattern=expense.description_raw[:200],
                normalized_merchant=normalized,
                category=category,
                subcategory=subcategory,
                source="user_override",
                confidence=Decimal("1.0"),
                match_count=1,
                last_matched_at=datetime.utcnow(),
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(rule)

        await self.db.commit()
        await self.db.refresh(expense)

        logger.info(f"Applied user override to expense {expense_id}: {category}/{subcategory}")
        return expense

    # -----------------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------------

    def _normalize_description(self, description: str) -> str:
        """Normalize description for rule matching.

        Uses CategoryEngine.normalize() to ensure consistency with
        how rules are looked up during PDF upload categorization.
        """
        from app.services.category_engine import CategoryEngine
        return CategoryEngine.normalize(description)

    async def get_statistics(self, user_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None) -> Dict[str, Any]:
        """
        Get expense statistics for a date range.
        """
        query = select(DailyExpense).where(DailyExpense.user_id == user_id)

        filters = []
        if date_from:
            filters.append(DailyExpense.transaction_date >= date_from)
        if date_to:
            filters.append(DailyExpense.transaction_date <= date_to)

        if filters:
            query = query.where(and_(*filters))

        result = await self.db.execute(query)
        expenses = list(result.scalars().all())

        total_amount = sum(e.amount for e in expenses)
        category_breakdown = {}
        payment_method_breakdown = {}

        for expense in expenses:
            # Category breakdown
            cat = expense.category or "Uncategorized"
            if cat not in category_breakdown:
                category_breakdown[cat] = Decimal("0")
            category_breakdown[cat] += expense.amount

            # Payment method breakdown
            pm = expense.payment_method
            if pm not in payment_method_breakdown:
                payment_method_breakdown[pm] = Decimal("0")
            payment_method_breakdown[pm] += expense.amount

        return {
            "total_count": len(expenses),
            "total_amount": float(total_amount),
            "category_breakdown": {k: float(v) for k, v in category_breakdown.items()},
            "payment_method_breakdown": {k: float(v) for k, v in payment_method_breakdown.items()},
            "average_expense": float(total_amount / len(expenses)) if expenses else 0,
        }
