"""
Smart Category Engine.
Replaces scikit-learn TF-IDF with a persistent rule memory system
backed by Claude AI (claude-haiku) for new merchants.

Flow:
  1. Normalize merchant name
  2. Check category_rules table (zero tokens — free)
  3. If no match → call Claude Haiku (cheapest model) to categorize
  4. Store result as a category_rules row (source="claude_ai")
  5. If user later overrides → update/insert with source="user_override", confidence=1.0
     → future transactions from same merchant auto-match this rule
"""
import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CategoryRule, Transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bangladesh-relevant seed rules
# ---------------------------------------------------------------------------

SEED_RULES = [
    # Groceries & Online Grocery
    ("chaldal", "Groceries", "Online Grocery", "user_override"),
    ("shajgoj", "Groceries", "Online Grocery", "user_override"),
    ("meena bazar", "Groceries", "Supermarket", "user_override"),
    ("shwapno", "Groceries", "Supermarket", "user_override"),
    ("agora", "Groceries", "Supermarket", "user_override"),
    ("unimart", "Groceries", "Supermarket", "user_override"),
    ("amana big bazar", "Groceries", "Supermarket", "user_override"),
    ("momota super shop", "Groceries", "Supermarket", "user_override"),
    ("kc bazar", "Groceries", "Supermarket", "user_override"),

    # Food & Dining
    ("transcom foods", "Food & Dining", "Restaurant", "user_override"),
    ("kfc", "Food & Dining", "Fast Food", "user_override"),
    ("pizza hut", "Food & Dining", "Fast Food", "user_override"),
    ("burger king", "Food & Dining", "Fast Food", "user_override"),
    ("pathao food", "Food & Dining", "Delivery", "user_override"),
    ("foodpanda", "Food & Dining", "Delivery", "user_override"),
    ("sunmoon pharma", "Food & Dining", "Pharmacy / Food", "user_override"),

    # Transport & Fuel
    ("sheba green filling", "Transport", "Fuel", "user_override"),
    ("intraco cng", "Transport", "CNG / Fuel", "user_override"),
    ("brac aarong", "Shopping", "Clothing", "user_override"),
    ("padma oil", "Transport", "Fuel", "user_override"),
    ("meghna petroleum", "Transport", "Fuel", "user_override"),
    ("uber", "Transport", "Ride Share", "user_override"),
    ("pathao", "Transport", "Ride Share", "user_override"),
    ("shohoz", "Transport", "Ride Share", "user_override"),

    # Health & Medical
    ("ibn sina", "Health", "Hospital / Diagnostic", "user_override"),
    ("square hospital", "Health", "Hospital", "user_override"),
    ("popular diagnostic", "Health", "Diagnostic", "user_override"),
    ("safeway pharma", "Health", "Pharmacy", "user_override"),
    ("sunmoon pharma and super shop", "Health", "Pharmacy", "user_override"),
    ("aspara", "Health", "Pharmacy", "user_override"),

    # Utilities & Mobile
    ("robi.com", "Utilities", "Mobile Recharge", "user_override"),
    ("grameenphone", "Utilities", "Mobile Recharge", "user_override"),
    ("banglalink", "Utilities", "Mobile Recharge", "user_override"),
    ("teletalk", "Utilities", "Mobile Recharge", "user_override"),
    ("desco", "Utilities", "Electricity", "user_override"),
    ("desco prepaid", "Utilities", "Electricity", "user_override"),
    ("wasa", "Utilities", "Water", "user_override"),

    # Shopping
    ("apex footwear", "Shopping", "Footwear", "user_override"),
    ("bata shoe", "Shopping", "Footwear", "user_override"),
    ("brand zone", "Shopping", "Clothing", "user_override"),
    ("fair cosmetics", "Shopping", "Beauty & Cosmetics", "user_override"),
    ("maisha enterprise", "Shopping", "Shopping", "user_override"),
    ("miclo bangladesh", "Shopping", "Shopping", "user_override"),
    ("sanvees", "Shopping", "Shopping", "user_override"),
    ("daraz", "Shopping", "Online Shopping", "user_override"),

    # Software & Dev Tools
    ("cursor.ai", "Software & Tools", "Dev Tools", "user_override"),
    ("github", "Software & Tools", "Dev Tools", "user_override"),
    ("openai", "Software & Tools", "AI Services", "user_override"),
    ("claude.ai", "Software & Tools", "AI Services", "user_override"),
    ("anthropic", "Software & Tools", "AI Services", "user_override"),
    ("canva", "Software & Tools", "Design Tools", "user_override"),
    ("figma", "Software & Tools", "Design Tools", "user_override"),
    ("notion", "Software & Tools", "Productivity", "user_override"),
    ("1password", "Software & Tools", "Security", "user_override"),
    ("adobe", "Software & Tools", "Design Tools", "user_override"),

    # Freelancing
    ("upwork", "Freelancing", "Platform Fee", "user_override"),
    ("fiverr", "Freelancing", "Platform Fee", "user_override"),
    ("toptal", "Freelancing", "Platform Fee", "user_override"),

    # Entertainment & Streaming
    ("netflix", "Entertainment", "Streaming Video", "user_override"),
    ("spotify", "Entertainment", "Streaming Music", "user_override"),
    ("youtube premium", "Entertainment", "Streaming Video", "user_override"),
    ("youtubepremium", "Entertainment", "Streaming Video", "user_override"),
    ("amazon prime", "Entertainment", "Streaming Video", "user_override"),
    ("disney", "Entertainment", "Streaming Video", "user_override"),
    ("apple music", "Entertainment", "Streaming Music", "user_override"),

    # Cloud & Hosting
    ("google cloud", "Software & Tools", "Cloud Services", "user_override"),
    ("amazon web services", "Software & Tools", "Cloud Services", "user_override"),
    ("aws", "Software & Tools", "Cloud Services", "user_override"),
    ("digitalocean", "Software & Tools", "Cloud Services", "user_override"),
    ("namecheap", "Software & Tools", "Domain / Hosting", "user_override"),
    ("godaddy", "Software & Tools", "Domain / Hosting", "user_override"),

    # Google services
    ("google play", "Software & Tools", "App Store", "user_override"),
    ("google one", "Software & Tools", "Cloud Storage", "user_override"),
    ("google storage", "Software & Tools", "Cloud Storage", "user_override"),

    # Financial / Fees
    ("annual fees", "Fees & Charges", "Annual Fee", "builtin"),
    ("annual fee", "Fees & Charges", "Annual Fee", "builtin"),
    ("late fee", "Fees & Charges", "Late Payment Fee", "builtin"),
    ("vat on annual", "Fees & Charges", "Tax", "builtin"),
    ("vat on online", "Fees & Charges", "Tax", "builtin"),
    ("finance charge", "Fees & Charges", "Interest", "builtin"),
]


# ---------------------------------------------------------------------------
# Category Engine
# ---------------------------------------------------------------------------

class CategoryEngine:
    """
    Persistent category rule lookup with Claude Haiku fallback.
    Thread-safe for async use.
    """

    # Standard category list for Claude to choose from
    CATEGORIES = [
        "Groceries", "Food & Dining", "Transport", "Health",
        "Utilities", "Shopping", "Software & Tools", "Freelancing",
        "Entertainment", "Fees & Charges", "Financial Services",
        "Travel & Hotels", "Education", "Insurance", "Charity",
        "Government & Tax", "Other",
    ]

    def __init__(self, db: AsyncSession):
        self.db = db
        self._claude_client = None

    def _get_claude_client(self):
        """Get or create AsyncAnthropic client for proper async support."""
        if self._claude_client is None and settings.anthropic_api_key:
            import anthropic
            # Use AsyncAnthropic for proper async/await support
            self._claude_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._claude_client

    async def categorize(
        self,
        merchant_name: Optional[str],
        description_raw: str,
        country: str = "BD",
        user_id: Optional[int] = None,
    ) -> Tuple[str, Optional[str], str, float]:
        """
        Categorize a transaction.

        Returns:
            (category, subcategory, source, confidence)
            source: "rule" | "claude_ai" | "builtin" | "fallback"
        """
        normalized = self._normalize(merchant_name or description_raw)

        # Step 1: Check rules table
        rule = await self._lookup_rule(normalized, user_id)
        if rule:
            # Update hit stats (fire and forget)
            rule.match_count += 1
            rule.last_matched_at = datetime.utcnow()
            await self.db.flush()
            return rule.category, rule.subcategory, "rule", float(rule.confidence)

        # Step 2: Claude Haiku categorization (cheapest model)
        if settings.anthropic_api_key:
            try:
                category, subcategory, confidence = await self._claude_categorize_with_retry(
                    merchant_name, description_raw, country
                )
                # Store as a new rule for future use
                await self._store_rule(
                    user_id=user_id,
                    merchant_pattern=merchant_name or description_raw,
                    normalized=normalized,
                    category=category,
                    subcategory=subcategory,
                    source="claude_ai",
                    confidence=confidence,
                )
                return category, subcategory, "claude_ai", confidence
            except Exception as e:
                logger.error(f"Claude categorization failed for '{merchant_name}': {type(e).__name__}: {e}")
                logger.debug(f"Full error details:", exc_info=True)

        # Step 3: Simple keyword fallback
        category = self._keyword_fallback(normalized)
        return category, None, "fallback", 0.5

    async def override_category(
        self,
        transaction_id: int,
        new_category: str,
        new_subcategory: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> bool:
        """
        Apply a user category override to a transaction and persist a rule.
        Future transactions from the same merchant will auto-match.
        """
        query = select(Transaction).where(Transaction.id == transaction_id)
        if user_id is not None:
            query = query.where(Transaction.user_id == user_id)
        result = await self.db.execute(query)
        transaction = result.scalar_one_or_none()
        if not transaction:
            return False

        # Update the transaction
        transaction.category_ai = new_category
        transaction.subcategory_ai = new_subcategory
        transaction.category_source = "user_override"
        transaction.category_manual = new_category
        transaction.merchant_category = new_category

        # Persist the override as a rule (highest priority)
        merchant = transaction.merchant_name or transaction.description_raw
        normalized = self._normalize(merchant)

        await self._upsert_user_override_rule(
            user_id=user_id,
            merchant_pattern=merchant,
            normalized=normalized,
            category=new_category,
            subcategory=new_subcategory,
        )

        await self.db.commit()
        return True

    async def _lookup_rule(self, normalized: str, user_id: Optional[int] = None) -> Optional[CategoryRule]:
        """Look up category_rules by normalized merchant with smart matching."""
        # Try exact match first (fastest)
        query = select(CategoryRule)
        if user_id is not None:
            query = query.where(CategoryRule.user_id == user_id)
        query = query.where(
                CategoryRule.normalized_merchant == normalized,
                CategoryRule.is_active == True,
            ).order_by(
                # user_override > claude_ai > builtin
                CategoryRule.source.desc(),
                CategoryRule.confidence.desc(),
                CategoryRule.match_count.desc(),
            ).limit(1)
        result = await self.db.execute(query)
        rule = result.scalar_one_or_none()
        if rule:
            return rule

        # Try prefix match (e.g., "github" matches "github inc san")
        # Use SQL LIKE query for efficiency
        query = select(CategoryRule).where(
                CategoryRule.is_active == True,
            )
        if user_id is not None:
            query = query.where(CategoryRule.user_id == user_id)
        result = await self.db.execute(
            query
            .order_by(
                # Prioritize: user_override > claude_ai > builtin, then by specificity
                CategoryRule.source.desc(),
                CategoryRule.confidence.desc(),
            )
        )
        all_rules = result.scalars().all()

        # Check if any rule is a prefix of the normalized string
        # Sort by length (longer = more specific) to match most specific first
        best_match = None
        best_length = 0

        for r in all_rules:
            if r.normalized_merchant:
                # Check if rule matches the start of the merchant name
                if normalized.startswith(r.normalized_merchant + " ") or normalized == r.normalized_merchant:
                    rule_len = len(r.normalized_merchant)
                    # Prefer longer (more specific) matches, and user_override over others
                    priority = (rule_len, r.source == "user_override", r.source == "claude_ai")
                    if rule_len > best_length or (rule_len == best_length and priority > (best_length, best_match and best_match.source == "user_override", best_match and best_match.source == "claude_ai")):
                        best_match = r
                        best_length = rule_len

        return best_match

    # ------------------------------------------------------------------
    # Batch categorization (single API call for all unmatched merchants)
    # ------------------------------------------------------------------

    async def batch_categorize(
        self,
        transactions: list[Dict[str, Any]],
        user_id: Optional[int] = None,
    ) -> list[Dict[str, Any]]:
        """
        Two-pass categorization:
          Pass 1 — match every transaction against the rules table (free).
          Pass 2 — send ALL unmatched transactions to Claude in ONE batch call.
        Updates each transaction dict in-place and returns it.
        """
        unmatched: list[Tuple[int, Dict[str, Any]]] = []  # (index, txn)

        # ---- Pass 1: rule matching ----
        for idx, txn in enumerate(transactions):
            if txn.get("category_source") == "user_override":
                continue

            merchant = txn.get("merchant_name") or txn.get("description_raw", "")
            normalized = self._normalize(merchant)
            rule = await self._lookup_rule(normalized, user_id)

            if rule:
                rule.match_count += 1
                rule.last_matched_at = datetime.utcnow()
                await self.db.flush()
                txn["category_ai"] = rule.category
                txn["subcategory_ai"] = rule.subcategory
                txn["category_source"] = "rule"
                txn["category_confidence"] = float(rule.confidence)
            else:
                # Keyword fallback first — it's free
                kw_cat = self._keyword_fallback(normalized)
                if kw_cat != "Other":
                    txn["category_ai"] = kw_cat
                    txn["subcategory_ai"] = None
                    txn["category_source"] = "builtin"
                    txn["category_confidence"] = 0.65
                else:
                    unmatched.append((idx, txn))

            # Keep merchant_category in sync
            if not txn.get("merchant_category") and txn.get("category_ai"):
                txn["merchant_category"] = txn["category_ai"]

        rule_matched = len(transactions) - len(unmatched)
        logger.info(
            f"Batch pass 1 (rules): {rule_matched} matched, "
            f"{len(unmatched)} need Claude AI"
        )

        if not unmatched:
            return transactions

        # ---- Pass 2: single batch Claude call for all unmatched ----
        if not settings.anthropic_api_key:
            logger.warning("No Anthropic API key — falling back for all unmatched")
            for _, txn in unmatched:
                txn["category_ai"] = "Other"
                txn["subcategory_ai"] = None
                txn["category_source"] = "fallback"
                txn["category_confidence"] = 0.5
                if not txn.get("merchant_category"):
                    txn["merchant_category"] = "Other"
            return transactions

        try:
            ai_results = await self._batch_claude_categorize(unmatched)

            for list_idx, (txn_idx, txn) in enumerate(unmatched):
                result = ai_results.get(list_idx)
                if result:
                    cat = result.get("category", "Other")
                    subcat = result.get("subcategory")
                    conf = float(result.get("confidence", 0.8))

                    # Validate category
                    if cat not in self.CATEGORIES:
                        cat = "Other"

                    txn["category_ai"] = cat
                    txn["subcategory_ai"] = subcat
                    txn["category_source"] = "claude_ai"
                    txn["category_confidence"] = round(conf, 2)

                    # Store as rule for future reuse
                    merchant = txn.get("merchant_name") or txn.get("description_raw", "")
                    normalized = self._normalize(merchant)
                    await self._store_rule(
                        user_id=user_id,
                        merchant_pattern=merchant,
                        normalized=normalized,
                        category=cat,
                        subcategory=subcat,
                        source="claude_ai",
                        confidence=conf,
                    )
                else:
                    txn["category_ai"] = "Other"
                    txn["subcategory_ai"] = None
                    txn["category_source"] = "fallback"
                    txn["category_confidence"] = 0.5

                if not txn.get("merchant_category"):
                    txn["merchant_category"] = txn["category_ai"]

            logger.info(f"Batch pass 2 (Claude): {len(ai_results)} categorized in 1 API call")

        except Exception as e:
            logger.error(f"Batch Claude categorization failed: {e}", exc_info=True)
            for _, txn in unmatched:
                txn["category_ai"] = self._keyword_fallback(
                    self._normalize(txn.get("merchant_name") or txn.get("description_raw", ""))
                )
                txn["subcategory_ai"] = None
                txn["category_source"] = "fallback"
                txn["category_confidence"] = 0.5
                if not txn.get("merchant_category"):
                    txn["merchant_category"] = txn["category_ai"]

        return transactions

    async def _batch_claude_categorize(
        self,
        unmatched: list[Tuple[int, Dict[str, Any]]],
        max_retries: int = 3,
    ) -> Dict[int, Dict[str, Any]]:
        """
        Send ALL unmatched transactions to Claude Haiku in ONE API call.
        Returns {list_index: {category, subcategory, confidence}}.
        """
        import asyncio

        client = self._get_claude_client()
        if not client:
            raise ValueError("No Anthropic API key")

        categories_str = ", ".join(self.CATEGORIES)

        # Build numbered list of merchants
        lines = []
        for i, (_, txn) in enumerate(unmatched, 1):
            merchant = txn.get("merchant_name") or txn.get("description_raw", "")
            desc = txn.get("description_raw", "")
            country = txn.get("merchant_country") or "BD"
            lines.append(f"{i}. Merchant: \"{merchant}\" | Description: \"{desc}\" | Country: {country}")

        merchants_block = "\n".join(lines)

        prompt = f"""Categorize each financial transaction below into ONE category from this list:
{categories_str}

Transactions:
{merchants_block}

Return ONLY a valid JSON array with one object per transaction in the SAME order:
[
  {{"index": 1, "category": "...", "subcategory": "...", "confidence": 0.85}},
  ...
]

Rules:
- "subcategory" should be 2-4 words max (e.g., "Online Subscription", "Fast Food")
- "confidence" is 0.0-1.0
- If uncertain, use "Other" with low confidence
- No extra text, just the JSON array
"""

        for attempt in range(max_retries):
            try:
                response = await client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=max(2000, len(unmatched) * 80),
                    messages=[{"role": "user", "content": prompt}],
                )

                raw = response.content[0].text.strip()
                # Strip markdown code fences
                if raw.startswith("```"):
                    raw = "\n".join(raw.split("\n")[1:])
                if raw.endswith("```"):
                    raw = "\n".join(raw.split("\n")[:-1])

                results_array = json.loads(raw.strip())

                # Map back to list indices (0-based)
                results: Dict[int, Dict[str, Any]] = {}
                for item in results_array:
                    idx = item.get("index", 0) - 1  # Convert 1-based to 0-based
                    if 0 <= idx < len(unmatched):
                        results[idx] = item

                cost = (
                    (response.usage.input_tokens / 1_000_000 * 0.80)
                    + (response.usage.output_tokens / 1_000_000 * 4.00)
                )
                logger.info(
                    f"Batch Claude call: {len(unmatched)} transactions, "
                    f"${cost:.4f} USD "
                    f"({response.usage.input_tokens} in / {response.usage.output_tokens} out)"
                )
                return results

            except Exception as e:
                is_rate_limit = "rate" in str(e).lower() or "429" in str(e)
                if attempt < max_retries - 1 and is_rate_limit:
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"Batch rate limit (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise

        return {}

    # ------------------------------------------------------------------
    # Legacy single-transaction methods (kept for backward compat)
    # ------------------------------------------------------------------

    async def _claude_categorize_with_retry(
        self,
        merchant_name: Optional[str],
        description_raw: str,
        country: str,
        max_retries: int = 3,
    ) -> Tuple[str, Optional[str], float]:
        """Call Claude Haiku with retry logic for rate limiting."""
        import asyncio

        for attempt in range(max_retries):
            try:
                return await self._claude_categorize(merchant_name, description_raw, country)
            except Exception as e:
                error_name = type(e).__name__
                is_rate_limit = "rate" in str(e).lower() or "429" in str(e)

                if attempt < max_retries - 1 and is_rate_limit:
                    # Exponential backoff: 1s, 2s, 4s
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"Rate limit hit for '{merchant_name}' (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    # Re-raise on last attempt or non-rate-limit errors
                    raise

    async def _claude_categorize(
        self,
        merchant_name: Optional[str],
        description_raw: str,
        country: str,
    ) -> Tuple[str, Optional[str], float]:
        """Call Claude Haiku to categorize a merchant (async)."""
        client = self._get_claude_client()
        if not client:
            raise ValueError("No Anthropic API key")

        categories_str = ", ".join(self.CATEGORIES)
        prompt = (
            f"Categorize this financial transaction. "
            f"Merchant: {merchant_name or 'unknown'}. "
            f"Description: {description_raw}. "
            f"Country: {country}. "
            f"Choose ONE category from: {categories_str}. "
            f"Also provide a subcategory (2-4 words max). "
            f'Return JSON only: {{"category": "...", "subcategory": "...", "confidence": 0.0-1.0}}'
        )

        # Properly await async client call
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])

        data = json.loads(raw.strip())
        category = data.get("category", "Other")
        subcategory = data.get("subcategory")
        confidence = float(data.get("confidence", 0.8))

        logger.debug(f"Claude categorized '{merchant_name}' → {category} / {subcategory} ({confidence})")
        return category, subcategory, confidence

    async def _store_rule(
        self,
        merchant_pattern: str,
        normalized: str,
        category: str,
        subcategory: Optional[str],
        source: str,
        confidence: float,
        user_id: Optional[int] = None,
    ):
        """Store a new category rule (unless one already exists for this merchant)."""
        # Check for ANY existing rule for this merchant (regardless of source)
        query = select(CategoryRule).where(
                CategoryRule.normalized_merchant == normalized,
                CategoryRule.is_active == True,
            )
        if user_id is not None:
            query = query.where(CategoryRule.user_id == user_id)
        query = query.order_by(
                # Prefer keeping user_override rules, update others
                CategoryRule.source.desc(),
            ).limit(1)
        existing = await self.db.execute(query)
        rule = existing.scalar_one_or_none()

        if rule:
            # If existing rule is user_override, don't overwrite with AI
            if rule.source == "user_override":
                return
            # Update existing rule with new AI result
            rule.category = category
            rule.subcategory = subcategory
            rule.confidence = Decimal(str(confidence))
            rule.source = source
            rule.updated_at = datetime.utcnow()
        else:
            rule = CategoryRule(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                merchant_pattern=merchant_pattern[:200],
                normalized_merchant=normalized[:200],
                category=category,
                subcategory=subcategory,
                source=source,
                confidence=Decimal(str(confidence)),
                match_count=0,
                is_active=True,
            )
            self.db.add(rule)

        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            # Savepoint rollback — only this rule insert is lost,
            # the rest of the session (e.g. cached extractions) is safe.
            logger.debug(f"Skipped duplicate category rule for '{normalized}' (source={source})")

    async def _upsert_user_override_rule(
        self,
        merchant_pattern: str,
        normalized: str,
        category: str,
        subcategory: Optional[str],
        user_id: Optional[int] = None,
    ):
        """Upsert a user override rule (always wins, confidence=1.0)."""
        query = select(CategoryRule).where(
                CategoryRule.normalized_merchant == normalized,
                CategoryRule.source == "user_override",
            )
        if user_id is not None:
            query = query.where(CategoryRule.user_id == user_id)
        existing = await self.db.execute(query)
        rule = existing.scalar_one_or_none()

        if rule:
            rule.category = category
            rule.subcategory = subcategory
            rule.confidence = Decimal("1.00")
            rule.updated_at = datetime.utcnow()
        else:
            rule = CategoryRule(
                uuid=str(uuid.uuid4()),
                user_id=user_id,
                merchant_pattern=merchant_pattern[:200],
                normalized_merchant=normalized[:200],
                category=category,
                subcategory=subcategory,
                source="user_override",
                confidence=Decimal("1.00"),
                match_count=0,
                is_active=True,
            )
            self.db.add(rule)

        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            # Savepoint rollback — only this rule insert is lost.
            logger.debug(f"Skipped duplicate user_override rule for '{normalized}'")

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize merchant/description text for rule matching.

        Public static method so all code paths (category engine,
        categories router, daily expense service) produce identical
        normalized strings.
        """
        if not text:
            return ""
        # Lowercase
        s = text.lower()
        # Remove accents
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        # Remove common prefixes from Amex descriptions
        s = re.sub(r"^purchase,", "", s)
        s = re.sub(r"^merchandize return,", "", s)
        s = re.sub(r"^merchandise return,", "", s)
        # Keep only alphanumeric and spaces
        s = re.sub(r"[^a-z0-9\s]", " ", s)
        # Collapse whitespace
        s = re.sub(r"\s+", " ", s).strip()
        # Take first 3 tokens (usually the merchant name before city/country)
        tokens = s.split()
        return " ".join(tokens[:3])

    def _normalize(self, text: str) -> str:
        """Instance wrapper kept for backward compat."""
        return self.normalize(text)

    def _keyword_fallback(self, normalized: str) -> str:
        """Simple keyword-based fallback (no API call)."""
        keywords = {
            "Groceries": ["grocery", "bazar", "shop", "mart", "super"],
            "Food & Dining": ["food", "restaurant", "cafe", "kitchen", "dining"],
            "Transport": ["cng", "fuel", "petrol", "uber", "pathao", "taxi"],
            "Health": ["pharma", "hospital", "clinic", "diagnostic", "medical"],
            "Utilities": ["robi", "grameenphone", "banglalink", "electricity", "water", "desco", "wasa"],
            "Entertainment": ["netflix", "spotify", "youtube", "disney"],
            "Software & Tools": ["cursor", "github", "openai", "claude", "canva"],
            "Fees & Charges": [
                "annual fee", "late fee", "vat on", "finance charge",
                "issuance fee", "card fee", "service charge", "debit card fee",
                "sms charge", "excise duty", "stamp duty",
            ],
            "Financial Services": [
                "fund transfer", "atm withdrawal", "atm", "npsb", "beftn", "rtgs",
                "online transfer", "transfer", "bkash", "nagad", "rocket",
                "mobile banking", "internet banking",
            ],
        }
        for category, patterns in keywords.items():
            if any(p in normalized for p in patterns):
                return category
        return "Other"


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

async def seed_category_rules(db: AsyncSession, user_id: Optional[int] = None):
    """
    Seed the category_rules table with Bangladesh-relevant merchant rules.
    Called once on startup (idempotent — skips existing entries).
    """
    inserted = 0
    for merchant, category, subcategory, source in SEED_RULES:
        normalized = re.sub(r"[^a-z0-9\s]", " ", merchant.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        # Take first 3 tokens
        normalized = " ".join(normalized.split()[:3])

        existing = await db.execute(
            select(CategoryRule).where(
                CategoryRule.normalized_merchant == normalized,
                CategoryRule.source == source,
            )
        )
        if existing.scalar_one_or_none():
            continue  # Already seeded

        rule = CategoryRule(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            merchant_pattern=merchant,
            normalized_merchant=normalized,
            category=category,
            subcategory=subcategory,
            source=source,
            confidence=Decimal("0.95"),
            match_count=0,
            is_active=True,
        )
        db.add(rule)
        inserted += 1

    if inserted > 0:
        await db.commit()
        logger.info(f"Seeded {inserted} category rules")
    else:
        logger.debug("Category rules already seeded")
