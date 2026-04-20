"""
Data normalizer for Claude Vision extraction results.
Converts extracted strings/floats into proper Python types,
auto-registers unseen Account records,
prettifies merchant names,
and prepares data for the database writer.
"""
import re
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Account, Statement
from app.services.vision.extraction_schema import (
    ExtractionResult, ExtractedCardSection, ExtractedTransaction,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known OCR corrections  (lowercase mis-read → lowercase correct)
# ---------------------------------------------------------------------------
_OCR_CORRECTIONS: Dict[str, str] = {
    "chatdai":   "chaldal",
    "chatdal":   "chaldal",
    "chaidai":   "chaldal",
    "chaldai":   "chaldal",
    "netlfix":   "netflix",
    "netflx":    "netflix",
    "amazan":    "amazon",
    "gogle":     "google",
    "googie":    "google",
    "spotlfy":   "spotify",
    "upwok":     "upwork",
    "fivver":    "fiverr",
    "fiver":     "fiverr",
}

# ---------------------------------------------------------------------------
# Canonical display names  (normalised key → display name)
# Applied after domain stripping and numeric-ID removal.
# ---------------------------------------------------------------------------
_DISPLAY_NAMES: Dict[str, str] = {
    "netflix":           "Netflix",
    "spotify":           "Spotify",
    "youtube":           "YouTube",
    "youtubepremium":    "YouTube Premium",
    "googlepay":         "Google Pay",
    "googleplay":        "Google Play",
    "googleone":         "Google One",
    "googlestorage":     "Google Storage",
    "google":            "Google",
    "amazon":            "Amazon",
    "amazonprime":       "Amazon Prime",
    "amazonwebservices": "Amazon Web Services",
    "aws":               "AWS",
    "upwork":            "Upwork",
    "fiverr":            "Fiverr",
    "toptal":            "Toptal",
    "chaldal":           "Chaldal",
    "shajgoj":           "Shajgoj",
    "daraz":             "Daraz",
    "robi":              "Robi",
    "grameenphone":      "Grameenphone",
    "banglalink":        "Banglalink",
    "teletalk":          "Teletalk",
    "bkash":             "bKash",
    "nagad":             "Nagad",
    "rocket":            "Rocket",
    "cursor":            "Cursor",
    "cursorai":          "Cursor AI",
    "github":            "GitHub",
    "openai":            "OpenAI",
    "anthropic":         "Anthropic",
    "chatgpt":           "ChatGPT",
    "claude":            "Claude AI",
    "canva":             "Canva",
    "figma":             "Figma",
    "notion":            "Notion",
    "1password":         "1Password",
    "adobe":             "Adobe",
    "digitalocean":      "DigitalOcean",
    "namecheap":         "Namecheap",
    "godaddy":           "GoDaddy",
    "cloudflare":        "Cloudflare",
    "vercel":            "Vercel",
    "netlify":           "Netlify",
    "kfc":               "KFC",
    "pathao":            "Pathao",
    "uber":              "Uber",
    "foodpanda":         "Foodpanda",
    "shohoz":            "Shohoz",
    "paypal":            "PayPal",
    "stripe":            "Stripe",
    "apple":             "Apple",
    "microsoft":         "Microsoft",
    "samsung":           "Samsung",
    "meena":             "Meena Bazar",
    "shwapno":           "Shwapno",
    "transcom":          "Transcom",
    "ibnsina":           "Ibn Sina",
    "populardx":         "Popular Diagnostic",
    "squarehospital":    "Square Hospital",
    "apex":              "Apex Footwear",
    "bata":              "Bata",
    "aarong":            "Aarong",
    "desco":             "DESCO",
    "wasa":              "WASA",
}

# Domain extension pattern to strip
_DOMAIN_RE = re.compile(
    r"\.(com|net|org|io|co|bd|com\.bd|gov\.bd|edu\.bd|app|ai|tech)(\b|$)",
    re.IGNORECASE
)
# Trailing numeric IDs (5+ digits, or short reference codes)
_TRAILING_NUM_RE = re.compile(r"\s+\d{4,}\s*$")
_EMBEDDED_NUM_RE = re.compile(r"\s+\d{4,}")


class DataNormalizer:
    """
    Converts ExtractionResult into dicts ready for Statement + Transaction
    database records, matching (or auto-registering) card sections to
    Account rows, and prettifying merchant names for display.
    """

    def __init__(self, db: AsyncSession, institution=None):
        self.db = db
        self.institution = institution

    async def normalize(
        self,
        user_id: int,
        result: ExtractionResult,
        filename: str,
        file_hash: str,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Normalize extraction result into the format expected by StatementService.

        Returns a dict with keys:
          metadata, transactions, fees, interest_charges,
          rewards_data, card_sections_meta, unmatched_cards
        """
        header = result.header
        summary = result.account_summary
        rewards = result.rewards_data

        metadata = self._build_metadata(header, summary, filename)

        all_transactions: List[Dict] = []
        card_sections_meta: List[Dict] = []
        unmatched_cards: List[Dict] = []

        # Track primary account id so supplement cards can be linked
        primary_account_id: Optional[int] = None

        for i, section in enumerate(result.all_card_sections):
            account_id, was_existing, auto_registered = await self._resolve_account(
                user_id,
                section.card_number_masked,
                section.cardholder_name,
                parent_account_id=primary_account_id if i > 0 else None,
            )

            if i == 0 and account_id:
                primary_account_id = account_id

            if not was_existing and not auto_registered:
                unmatched_cards.append({
                    "card_number_masked": section.card_number_masked,
                    "cardholder_name": section.cardholder_name,
                })

            for txn in section.transactions:
                # Skip fee/payment/interest types
                if txn.transaction_type in ("fee", "payment", "interest"):
                    continue
                # Skip statement-summary rows (balance carry-forwards, payment acknowledgements)
                if self._is_summary_row(txn.description_raw, txn.merchant_name):
                    # Salvage values into metadata if those fields are still blank
                    amt = self._to_decimal(txn.billing_amount or txn.original_amount)
                    desc_lower = (txn.description_raw or "").lower()
                    if amt is not None and "previous" in desc_lower and not metadata.get("previous_balance"):
                        metadata["previous_balance"] = amt
                    if amt is not None and "new balance" in desc_lower and not metadata.get("new_balance"):
                        metadata["new_balance"] = amt
                    if amt is not None and "payment" in desc_lower and not metadata.get("payments_credits"):
                        metadata["payments_credits"] = amt
                    continue
                normalized = self._normalize_transaction(txn, account_id, section)
                all_transactions.append(normalized)

            card_sections_meta.append({
                "card_number_masked": section.card_number_masked,
                "cardholder_name": section.cardholder_name,
                "account_id": account_id,
                "auto_registered": auto_registered,
                "transaction_count": len(section.transactions),
            })

        # Fees
        fees: List[Dict] = []
        for page in result.pages:
            if page.fees_section:
                for f in page.fees_section:
                    fees.append(self._normalize_fee(f))
            if page.card_sections:
                for section in page.card_sections:
                    for txn in section.transactions:
                        if txn.transaction_type == "fee":
                            fees.append(self._normalize_fee(txn))

        # Payments
        payments: List[Dict] = []
        for page in result.pages:
            if page.payments_section:
                for p in page.payments_section:
                    payments.append(self._normalize_payment(p))
            if page.card_sections:
                for section in page.card_sections:
                    for txn in section.transactions:
                        if txn.transaction_type == "payment":
                            payments.append(self._normalize_payment(txn))

        # Rewards
        rewards_dict = None
        if rewards:
            rewards_dict = {
                "reward_program_name": rewards.program_name,
                "opening_balance":     rewards.opening_balance or 0,
                "earned_purchases":    rewards.earned_purchases or 0,
                "earned_bonus":        rewards.earned_bonus or 0,
                "earned_welcome":      0,
                "redeemed_travel":     0,
                "redeemed_cashback":   0,
                "redeemed_vouchers":   0,
                "redeemed_other":      rewards.redeemed or 0,
                "expired":             rewards.expired or 0,
                "expired_this_period": rewards.expired_this_period or 0,
                "adjusted":            rewards.adjustment or 0,
                "closing_balance":     rewards.closing_balance or 0,
                "accelerated_tiers":   rewards.accelerated_tiers,
                "points_expiring_next_month": rewards.points_expiring_next_month or 0,
            }

        return {
            "metadata":          metadata,
            "transactions":      all_transactions,
            "fees":              fees,
            "interest_charges":  [],
            "payments":          payments,
            "rewards_data":      rewards_dict,
            "card_sections_meta": card_sections_meta,
            "unmatched_cards":   unmatched_cards,
            "extraction_method": "claude_vision",
            "ai_confidence":     result.confidence,
        }

    # ------------------------------------------------------------------
    # Account resolution (match existing → auto-register)
    # ------------------------------------------------------------------

    async def _resolve_account(
        self,
        user_id: int,
        card_number_masked: str,
        cardholder_name: str = "",
        parent_account_id: Optional[int] = None,
    ) -> Tuple[Optional[int], bool, bool]:
        """
        Find or auto-create an Account for the given masked card number.

        Returns:
            (account_id, was_previously_existing, was_auto_registered_now)
        """
        if not card_number_masked or card_number_masked == "unknown":
            return None, False, False

        last_four = card_number_masked.replace("*", "").replace("-", "").replace(" ", "")[-4:]

        result = await self.db.execute(
            select(Account).where(
                Account.user_id == user_id,
                Account.account_number_masked.contains(last_four),
                Account.is_active == True,
            )
        )
        account = result.scalar_one_or_none()

        if account:
            logger.debug(f"Matched {card_number_masked} → Account id={account.id}")
            return account.id, True, False

        # Auto-register
        network = self._detect_card_network(card_number_masked)
        tier = "supplement" if parent_account_id else "primary"
        currency = self.institution.default_currency if self.institution else "BDT"
        institution_id = self.institution.id if self.institution else None

        name_parts = cardholder_name.strip().split() if cardholder_name else []
        first_name = name_parts[0].title() if name_parts else "Card"
        nickname = f"{first_name} •••• {last_four}"

        new_account = Account(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            institution_id=institution_id,
            account_type="credit_card",
            account_number_masked=card_number_masked,
            cardholder_name=cardholder_name.title() if cardholder_name else None,
            account_nickname=nickname,
            card_network=network,
            card_tier=tier,
            parent_account_id=parent_account_id,
            billing_currency=currency,
            is_active=True,
        )
        self.db.add(new_account)
        await self.db.flush()

        logger.info(
            f"Auto-registered Account id={new_account.id} "
            f"({card_number_masked}, {cardholder_name}, {tier})"
        )
        return new_account.id, False, True

    @staticmethod
    def _detect_card_network(card_number_masked: str) -> Optional[str]:
        """Infer card network from the first visible digit(s)."""
        digits = re.sub(r"[^0-9]", "", card_number_masked)
        if not digits:
            return None
        first = digits[0]
        if first == "3":
            return "AMEX"
        if first == "4":
            return "VISA"
        if first == "5":
            return "MASTERCARD"
        if first == "6":
            return "DISCOVER"
        return None

    # ------------------------------------------------------------------
    # Merchant name prettification
    # ------------------------------------------------------------------

    @staticmethod
    def _prettify_merchant(
        merchant_name: Optional[str],
        description_raw: str,
    ) -> str:
        """
        Return a clean, human-readable merchant display name.

        Pipeline:
        1. Extract merchant from description_raw if merchant_name is empty/looks like description
        2. Apply OCR corrections (chatdai → chaldal)
        3. Strip domain extensions (.com, .com.bd …)
        4. Strip trailing numeric IDs (545706, etc.)
        5. Look up canonical display name (netflix → Netflix)
        6. Title-case fallback
        """
        raw = merchant_name or ""

        # If merchant_name looks like the full description, extract the real part
        if not raw or raw.lower().startswith("purchase,") or "," in raw:
            raw = DataNormalizer._extract_merchant_segment(description_raw) or raw

        if not raw:
            return description_raw.split(",")[0].strip().title() or "Unknown"

        lower = raw.lower().strip()

        # 1. Full-string OCR correction
        if lower in _OCR_CORRECTIONS:
            lower = _OCR_CORRECTIONS[lower]
        else:
            # Per-word correction
            words = lower.split()
            words = [_OCR_CORRECTIONS.get(w, w) for w in words]
            lower = " ".join(words)

        # 2. Strip domain extensions
        lower = _DOMAIN_RE.sub("", lower).strip()

        # 3. Strip trailing / embedded numeric IDs
        lower = _TRAILING_NUM_RE.sub("", lower).strip()
        lower = _EMBEDDED_NUM_RE.sub("", lower).strip()

        # 4. Look up display name (try no-space key, then first-word key)
        no_space_key = re.sub(r"\s+", "", lower)
        if no_space_key in _DISPLAY_NAMES:
            return _DISPLAY_NAMES[no_space_key]

        first_word_key = lower.split()[0] if lower.split() else ""
        if first_word_key in _DISPLAY_NAMES:
            return _DISPLAY_NAMES[first_word_key]

        # 5. Title-case with common abbreviation fixes
        result = lower.title()
        result = re.sub(r"\bLtd\b", "Ltd.", result)
        result = re.sub(r"\bInc\b", "Inc.", result)
        result = re.sub(r"\bBd\b", "BD", result)
        return result or raw.title()

    @staticmethod
    def _extract_merchant_segment(description: str) -> Optional[str]:
        """
        Pull the merchant name out of a raw description string.

        "Purchase,chaldal,dhaka zila,bangladesh"  → "chaldal"
        "Purchase,netflix,united states"           → "netflix"
        "Payment received with thank"              → None (not a purchase)
        "Trn. Br: 095 Debit Card Issuance Fees"   → "Debit Card Issuance Fees"
        "Trn. Br: 789 462870******6111 ..."        → None (pure reference, no merchant)
        """
        if not description:
            return None

        lower = description.lower().strip()

        # Standard comma-delimited formats (credit card statements)
        for prefix in (
            "purchase,",
            "merchandize return,",
            "merchandise return,",
            "pos purchase,",
            "online purchase,",
        ):
            if lower.startswith(prefix):
                remainder = description[len(prefix):].strip()
                # First comma-delimited segment = merchant name
                segment = remainder.split(",")[0].strip()
                return segment if segment else None

        # Bangladeshi bank statement format: "Trn. Br: XXX [description] [reference numbers]"
        trn_match = re.match(
            r"Trn\.?\s*Br:?\s*\d{3}\s*(.*)",
            description,
            re.IGNORECASE,
        )
        if trn_match:
            remainder = trn_match.group(1).strip()
            # Remove masked card numbers (e.g. 462870******6111)
            remainder = re.sub(r"\d{4,}\*{2,}\d{2,}", "", remainder)
            # Remove long reference numbers (10+ digits)
            remainder = re.sub(r"\b\d{10,}\b", "", remainder)
            # Remove short numeric-only tokens (sequences of digits possibly with dots/commas)
            remainder = re.sub(r"\b[\d,.]{6,}\b", "", remainder)
            # Remove alphanumeric codes like UCBMP054
            remainder = re.sub(r"\b[A-Z]{2,}\d{3,}\b", "", remainder)
            remainder = remainder.strip(" -,")
            if remainder and not remainder.replace(" ", "").isdigit():
                return remainder
            return None

        return None

    # ------------------------------------------------------------------
    # Transaction / fee / payment normalizers
    # ------------------------------------------------------------------

    def _build_metadata(self, header, summary, filename: str) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "bank_name":         "Unknown",
            "account_number":    "unknown",
            "currency":          settings.default_currency,
            "extraction_method": "claude_vision",
        }

        if header:
            meta["bank_name"]              = header.bank_name or "Unknown"
            meta["cardholder_name"]        = header.cardholder_name
            meta["account_number"]         = header.card_number_masked or "unknown"
            meta["statement_date"]         = self._parse_date(header.statement_date)
            meta["payment_due_date"]       = self._parse_date(header.payment_due_date)
            meta["statement_period_from"]  = self._parse_date(header.statement_period_from)
            meta["statement_period_to"]    = self._parse_date(header.statement_period_to)

        if summary:
            meta["previous_balance"]       = self._to_decimal(summary.previous_balance)
            meta["payments_credits"]       = self._to_decimal(summary.payment_received)
            meta["new_balance"]            = self._to_decimal(summary.new_balance)
            meta["credit_limit"]           = self._to_decimal(summary.credit_limit)
            meta["available_credit"]       = self._to_decimal(summary.available_credit)
            meta["cash_advance_limit"]     = self._to_decimal(summary.cash_limit)
            meta["total_amount_due"]       = self._to_decimal(
                summary.total_outstanding or summary.current_balance
            )
            meta["minimum_payment_due"]    = self._to_decimal(summary.minimum_amount_due)
            meta["rewards_closing"]        = int(summary.reward_points) if summary.reward_points else None

        return meta

    # Descriptions that indicate a statement-summary row, not a real merchant transaction.
    _SUMMARY_ROW_PATTERNS = (
        "previous balance",
        "new balance",
        "closing balance",
        "opening balance",
        "payment received",
        "payment received with thanks",
        "payment - thank you",
        "payment thank you",
        "auto payment",
        "balance forward",
        "balance b/f",
        "balance brought forward",
        "carried forward",
        "carry forward",
        "balance transfer",
        "credit adjustment",
        "debit adjustment",
        "finance charge",
        "late payment fee",        # handled by fees_section, skip here
        "minimum payment due",
        "total due",
        "total amount due",
        "statement balance",
    )

    @classmethod
    def _is_summary_row(cls, description_raw: Optional[str], merchant_name: Optional[str]) -> bool:
        """Return True if the row is a balance/payment summary entry, not a real purchase."""
        text = (description_raw or merchant_name or "").lower().strip()
        if not text:
            return False
        return any(text == p or text.startswith(p) for p in cls._SUMMARY_ROW_PATTERNS)

    def _normalize_transaction(
        self,
        txn: ExtractedTransaction,
        account_id: Optional[int],
        section: ExtractedCardSection,
    ) -> Dict[str, Any]:
        billing_amount  = self._to_decimal(txn.billing_amount)
        original_amount = self._to_decimal(txn.original_amount)

        fx_rate = None
        if (
            txn.original_currency
            and txn.billing_currency
            and txn.original_currency != txn.billing_currency
            and original_amount
            and original_amount != 0
        ):
            try:
                fx_rate = billing_amount / original_amount
            except Exception:
                pass

        debit_credit = "C" if txn.is_credit else "D"

        # Prettify merchant name for display
        pretty_merchant = self._prettify_merchant(txn.merchant_name, txn.description_raw)

        return {
            "account_id":         account_id,
            "account_number":     section.card_number_masked,
            "card_last_four":     section.card_number_masked[-4:] if section.card_number_masked else None,
            "transaction_date":   self._parse_date(txn.date),
            "description_raw":    txn.description_raw,
            "merchant_name":      pretty_merchant,
            # Legacy fields
            "amount":             billing_amount,
            "currency":           txn.billing_currency or settings.default_currency,
            # New semantic fields
            "billing_amount":     billing_amount,
            "billing_currency":   txn.billing_currency or settings.default_currency,
            "original_amount":    original_amount if original_amount != billing_amount else None,
            "original_currency":  (
                txn.original_currency
                if txn.original_currency != txn.billing_currency
                else None
            ),
            "fx_rate_applied":    fx_rate,
            # Legacy FX
            "foreign_amount":     original_amount if original_amount != billing_amount else None,
            "foreign_currency":   (
                txn.original_currency
                if txn.original_currency != txn.billing_currency
                else None
            ),
            "exchange_rate":      fx_rate,
            "transaction_type":   txn.transaction_type,
            "debit_credit":       debit_credit,
            "reference_number":   txn.reference_number,
            "merchant_city":      txn.merchant_city,
            "merchant_country":   self._normalize_country(txn.merchant_country),
            "is_international":   (
                txn.original_currency != txn.billing_currency
                if txn.original_currency and txn.billing_currency
                else False
            ),
        }

    def _normalize_fee(self, txn: ExtractedTransaction) -> Dict[str, Any]:
        return {
            "fee_type":        txn.description_raw[:100],
            "fee_description": txn.description_raw,
            "amount":          self._to_decimal(txn.billing_amount),
            "currency":        txn.billing_currency or settings.default_currency,
        }

    def _normalize_payment(self, txn: ExtractedTransaction) -> Dict[str, Any]:
        return {
            "payment_date":   self._parse_date(txn.date),
            "payment_amount": self._to_decimal(txn.billing_amount),
            "currency":       txn.billing_currency or settings.default_currency,
            "payment_method": txn.description_raw[:50] if txn.description_raw else None,
        }

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _parse_date(self, date_str: Optional[str]) -> Optional[date]:
        if not date_str:
            return None
        formats = [
            "%Y-%m-%d", "%d-%m-%Y", "%d %b, %Y", "%d %b %Y",
            "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%B %d, %Y",
        ]
        clean = date_str.strip()
        for fmt in formats:
            try:
                return datetime.strptime(clean, fmt).date()
            except ValueError:
                continue
        logger.warning(f"Could not parse date: {date_str!r}")
        return None

    def _to_decimal(self, value) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    def _normalize_country(self, country_str: Optional[str]) -> str:
        if not country_str:
            return "BD"
        mapping = {
            "bangladesh":    "BD",
            "united states": "US",
            "usa":           "US",
            "united kingdom": "GB",
            "uk":            "GB",
            "sweden":        "SE",
            "singapore":     "SG",
            "india":         "IN",
            "germany":       "DE",
            "netherlands":   "NL",
            "canada":        "CA",
            "australia":     "AU",
            "ireland":       "IE",
        }
        return mapping.get(country_str.lower(), country_str[:2].upper())
