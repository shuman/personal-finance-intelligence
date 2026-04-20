"""
Claude PDF extractor for financial statement PDFs.
Sends the full PDF as a native document block to Claude and parses the structured JSON response.

Model is configurable via config.extraction_model:
  - "claude-haiku-4-5"  — default, fast, cheap (~15x cheaper than Sonnet)
  - "claude-sonnet-4-5" — highest accuracy, higher cost

Token safety:
  - max_tokens is set via config.extraction_max_tokens (default 16000)
  - JSON repair handles truncated or malformed responses
  - One automatic retry with error feedback if parsing fails completely
"""
import base64
import json
import re
import logging
from typing import Optional

from app.config import settings
from app.services.vision.extraction_schema import (
    ExtractionResult, ExtractedPage, ExtractedCardSection,
    ExtractedTransaction, ExtractedAccountSummary, ExtractedRewardsData,
    ExtractedStatementHeader,
)

logger = logging.getLogger(__name__)

# Pricing per million tokens (as of 2026)
_PRICING = {
    "claude-haiku-4-5":  {"input": 0.80, "output": 4.00},
    "claude-haiku-3-5":  {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-3-5": {"input": 3.00, "output": 15.00},
}

EXTRACTION_SYSTEM_PROMPT = """You are a precise financial statement data extractor.
Your job is to extract all transaction and financial data from bank statement PDFs (credit cards, debit cards, savings accounts, current accounts).

RULES:
1. Return ONLY valid JSON. No markdown, no explanations, no extra text — just the JSON.
2. If a page contains ONLY legal disclaimers, terms of service, or promotional ads
   (no financial data), return: {"skip": true, "skip_reason": "reason"}
3. Extract ALL transactions visible. Do not miss any row.
4. For the card section header (e.g. "376948*****9844 JOBAER AHMED"), start a new card_section.
5. Source currency/amount = the "Source" column. Settlement = the "Settlement" or "Billing Amount" column.
6. For returns/credits, set is_credit=true.
7. Dates: return as "YYYY-MM-DD" if possible, otherwise return as-is.
8. Omit null fields to keep output compact and within token limits.

CRITICAL AMOUNT PARSING RULES:
9.  billing_amount and original_amount must be the ACTUAL monetary transaction amount only.
    NEVER include account numbers, reference numbers, branch codes, or sequence numbers as amounts.
10. For bank/debit statements: the Debit column = billing_amount (money spent),
    the Credit column = billing_amount with is_credit=true (money received).
    The Balance column is NOT the transaction amount — ignore it for billing_amount.
11. If a transaction row contains mixed numbers (e.g. "Trn. Br: 789 462870******6111 UCBMP054 000868680108 10,000.00"),
    the monetary amount is typically the LAST number that looks like a formatted currency value (with commas/decimals).
    Account numbers, reference numbers, and branch codes are NOT amounts.
12. Look for dedicated Debit/Credit/Amount columns in the statement table. Use those columns for the amount.

INTELLIGENT MERCHANT NAME EXTRACTION:
13. For bank statements with descriptions like "Trn. Br: 095 Debit Card Issuance Fees for the Card No462870******6111":
    - merchant_name should be the meaningful part: "Debit Card Issuance Fees"
    - Do NOT use branch codes ("Trn. Br: 095") as merchant names
14. For ATM/POS transactions, extract the terminal/location name as merchant_name.
15. For fund transfers, use the recipient/sender name or purpose as merchant_name.
16. For descriptions that are purely numeric references with no readable name, set merchant_name to a descriptive label
    based on transaction_type (e.g. "Fund Transfer", "ATM Withdrawal", "POS Purchase", "Online Transfer").

TRANSACTION TYPE DETECTION:
17. Classify transactions intelligently based on description:
    - "ATM" or "cash withdrawal" → transaction_type: "cash_advance"
    - "POS" or "purchase" → transaction_type: "purchase"
    - "transfer", "NPSB", "BEFTN", "fund transfer" → transaction_type: "transfer"
    - "fee", "charge", "issuance", "VAT" → transaction_type: "fee"
    - "interest" → transaction_type: "interest"
    - "salary", "deposit", credit entries → transaction_type: "deposit" with is_credit: true

STATEMENT STRUCTURE HINT: {format_hint}

Return a JSON ARRAY where each element is one page, using this structure per page:
{
  "page_type": "transaction|rewards|summary|skip",
  "skip": false,
  "header": {
    "bank_name": null, "cardholder_name": null, "card_number_masked": null,
    "statement_date": null, "payment_due_date": null,
    "statement_period_from": null, "statement_period_to": null
  },
  "account_summary": {
    "previous_balance": null, "payment_received": null, "new_balance": null,
    "credit_limit": null, "available_credit": null, "cash_limit": null,
    "minimum_amount_due": null, "total_outstanding": null, "reward_points": null
  },
  "card_sections": [
    {
      "card_number_masked": "376948*****9844",
      "cardholder_name": "JOBAER AHMED",
      "transactions": [
        {
          "date": "2026-02-23",
          "description_raw": "Purchase,upwork,san francisco,united states",
          "merchant_name": "Upwork",
          "merchant_city": "San Francisco",
          "merchant_country": "United States",
          "original_currency": "USD",
          "original_amount": 15.00,
          "billing_currency": "BDT",
          "billing_amount": 1856.25,
          "transaction_type": "purchase",
          "is_credit": false
        }
      ]
    }
  ],
  "fees_section": [],
  "payments_section": [],
  "rewards_data": {
    "program_name": null, "opening_balance": null, "earned_purchases": null,
    "closing_balance": null, "expired": null
  }
}"""


class ClaudeExtractor:
    """
    Sends a full PDF statement to Claude as a native document block and returns
    a structured ExtractionResult.
    """

    def __init__(self, institution=None, model: Optional[str] = None):
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set. Add it to your .env file.")
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.institution = institution
        self.model = model or settings.extraction_model

    def extract(self, pdf_bytes: bytes) -> ExtractionResult:
        """
        Extract data from the full PDF in a single Claude API call.
        Falls back to one retry if the initial response fails to parse.
        """
        if not pdf_bytes:
            return ExtractionResult()

        format_hint = self._get_format_hint()
        system_prompt = EXTRACTION_SYSTEM_PROMPT.replace("{format_hint}", format_hint)
        content = self._build_message_content(pdf_bytes)

        logger.info(
            f"Calling Claude {self.model} with full PDF document "
            f"(max_tokens={settings.extraction_max_tokens})"
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=settings.extraction_max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            raise ValueError(f"Claude PDF extraction failed: {e}")

        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        pricing       = _PRICING.get(self.model, {"input": 3.0, "output": 15.0})
        cost_usd      = (
            input_tokens  * pricing["input"] +
            output_tokens * pricing["output"]
        ) / 1_000_000

        # Warn if we hit the token ceiling (truncation likely)
        if output_tokens >= settings.extraction_max_tokens - 50:
            logger.warning(
                f"Output tokens ({output_tokens}) nearly hit the ceiling "
                f"({settings.extraction_max_tokens}). JSON may be truncated. "
                "Consider raising extraction_max_tokens or splitting large PDFs."
            )

        logger.info(
            f"Claude {self.model}: {input_tokens}↑ + {output_tokens}↓ tokens "
            f"= ${cost_usd:.5f} USD"
        )

        raw_text = response.content[0].text
        extracted_pages, issues = self._parse_response(raw_text)

        # One automatic retry if parsing yielded nothing
        if not extracted_pages and issues:
            logger.warning("Initial parse yielded nothing — retrying with error context")
            extracted_pages, issues = self._retry_with_feedback(
                pdf_bytes, system_prompt, raw_text, issues
            )

        # Report truncation in issues list if it occurred
        if output_tokens >= settings.extraction_max_tokens - 50:
            issues.append({
                "type": "possible_truncation",
                "detail": (
                    f"Output hit token ceiling ({output_tokens} tokens). "
                    "Some transactions may be missing. "
                    "Raise EXTRACTION_MAX_TOKENS in .env or split the PDF."
                ),
            })

        pages_skipped = sum(1 for p in extracted_pages if p.skip)

        return ExtractionResult(
            pages=extracted_pages,
            pages_skipped=pages_skipped,
            model_used=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # JSON parsing + repair
    # ------------------------------------------------------------------

    def _parse_response(self, raw_text: str) -> tuple:
        """
        Parse Claude's text response into ExtractedPage objects.
        Applies progressive repair before giving up.
        """
        extracted_pages = []
        issues = []

        clean = self._repair_json(raw_text)

        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            issues.append({"type": "json_parse_error", "detail": str(e), "raw": clean[:300]})
            return extracted_pages, issues

        if isinstance(data, dict):
            data = [data]

        for i, page_data in enumerate(data):
            page_num = i + 1
            try:
                ep = self._parse_page_data(page_data, page_num)
                extracted_pages.append(ep)
            except Exception as e:
                issues.append({"type": "page_parse_error", "page": page_num, "detail": str(e)})
                extracted_pages.append(
                    ExtractedPage(page_number=page_num, skip=True, skip_reason=str(e))
                )

        return extracted_pages, issues

    @staticmethod
    def _repair_json(raw: str) -> str:
        """
        Progressively repair common Claude JSON output problems:
        1. Strip markdown code fences
        2. Remove trailing commas  (,} or ,])
        3. Recover truncated arrays — find the last complete JSON object
           inside the array and close the array there

        Returns the best candidate string (may still be invalid JSON if
        all repair attempts fail — the caller will surface the error).
        """
        # 1. Strip markdown code fences
        text = raw.strip()
        # Handle ```json ... ``` or ``` ... ```
        text = re.sub(r'^```[a-zA-Z]*\n?', '', text).strip()
        text = re.sub(r'\n?```$', '', text).strip()

        # 2. Remove trailing commas before } or ]
        text = re.sub(r',(\s*[}\]])', r'\1', text)

        # 3. Quick check — already valid
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # 4. Recover truncated JSON array
        #    Walk through the text tracking bracket depth at the TOP level
        #    (depth=0 means we're inside the outer [ ]).
        #    Every time depth returns to 0 after a }, that's a complete element.
        if text.lstrip().startswith('['):
            # Find last position where a complete top-level object closed
            depth = 0
            in_string = False
            escape_next = False
            last_complete_pos = -1

            for idx, ch in enumerate(text):
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        last_complete_pos = idx  # this } closes a top-level object

            if last_complete_pos > 0:
                candidate = text[:last_complete_pos + 1].rstrip().rstrip(',') + ']'
                try:
                    json.loads(candidate)
                    logger.warning(
                        f"Repaired truncated JSON: recovered array up to char {last_complete_pos}"
                    )
                    return candidate
                except json.JSONDecodeError:
                    pass

        # 5. Recover truncated single object
        if text.lstrip().startswith('{'):
            opens  = text.count('{')
            closes = text.count('}')
            if opens > closes:
                candidate = text + '}' * (opens - closes)
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass

        return text  # Return best effort; will fail at json.loads with proper error

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _get_format_hint(self) -> str:
        if not self.institution:
            return (
                "Generic bank statement. Extract all transaction rows carefully. "
                "Card sections start with a card number header like '376948*****9844 HOLDER NAME'. "
                "For bank/debit account statements: look for Date, Description/Narration, Debit, Credit, Balance columns. "
                "Use the Debit or Credit column as billing_amount (NOT the running Balance column). "
                "Descriptions may contain branch codes (Trn. Br: XXX), account numbers, and reference numbers — "
                "these are NOT amounts. The actual amount is in the dedicated Debit/Credit column. "
                "Extract a meaningful merchant_name from the description — skip branch codes and reference numbers."
            )
        hints = {
            "city_bank_amex": (
                "City Bank American Express (Bangladesh). "
                "Transactions have Source (Currency + Amount) and Settlement (BDT Amount) columns. "
                "Card sections separated by bold card number headers. "
                "Primary card holder name style: 'JOBAER AHMED'. "
                "Supplement cards shown as '376948*****8528 BILKIS AKHTER'."
            ),
            "brac_visa": (
                "BRAC Bank Visa (Bangladesh). Sectioned layout: "
                "PAYMENTS section, INTERESTS FEES & VAT section, BASIC CARD transactions. "
                "All amounts in BDT. Reward Points Summary at bottom of last page."
            ),
        }
        return hints.get(
            self.institution.statement_format_hint,
            f"Bank: {self.institution.name}. Default currency: {self.institution.default_currency}. "
            f"Look for dedicated Debit/Credit/Amount columns. Do NOT confuse reference numbers or account numbers with amounts. "
            f"Extract meaningful merchant names from transaction descriptions."
        )

    def _build_message_content(self, pdf_bytes: bytes) -> list:
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": pdf_b64,
                },
            },
            {
                "type": "text",
                "text": (
                    "Extract all pages from this statement as a JSON array — "
                    "one element per page, in order. "
                    "Be concise: omit null/zero fields to stay within token limits. "
                    "JSON only — no other text."
                ),
            },
        ]

    # ------------------------------------------------------------------
    # Page-data parsing (unchanged logic)
    # ------------------------------------------------------------------

    def _parse_page_data(self, data: dict, page_num: int) -> ExtractedPage:
        if data.get("skip"):
            return ExtractedPage(
                page_number=page_num, skip=True,
                skip_reason=data.get("skip_reason", "Claude flagged as non-data page"),
            )

        header = None
        if data.get("header"):
            h = data["header"]
            header = ExtractedStatementHeader(
                bank_name=h.get("bank_name"),
                cardholder_name=h.get("cardholder_name"),
                card_number_masked=h.get("card_number_masked"),
                statement_date=h.get("statement_date"),
                payment_due_date=h.get("payment_due_date"),
                statement_period_from=h.get("statement_period_from"),
                statement_period_to=h.get("statement_period_to"),
                client_id=h.get("client_id"),
            )

        account_summary = None
        if data.get("account_summary"):
            s = data["account_summary"]
            account_summary = ExtractedAccountSummary(**{k: v for k, v in s.items() if v is not None})

        card_sections = []
        for section_data in data.get("card_sections") or []:
            transactions = []
            for txn_data in section_data.get("transactions") or []:
                try:
                    transactions.append(ExtractedTransaction(**txn_data))
                except Exception as e:
                    logger.warning(f"Skipping invalid transaction: {e} — {txn_data}")
            card_sections.append(ExtractedCardSection(
                card_number_masked=section_data.get("card_number_masked", "unknown"),
                cardholder_name=section_data.get("cardholder_name", ""),
                transactions=transactions,
            ))

        fees_section = []
        for txn_data in data.get("fees_section") or []:
            try:
                fees_section.append(ExtractedTransaction(**txn_data))
            except Exception:
                pass

        payments_section = []
        for txn_data in data.get("payments_section") or []:
            try:
                payments_section.append(ExtractedTransaction(**txn_data))
            except Exception:
                pass

        rewards_data = None
        if data.get("rewards_data"):
            r = data["rewards_data"]
            # Pre-process accelerated_tiers: Claude often returns a list of
            # tier objects but the Pydantic model expects a dict.
            tiers = r.get("accelerated_tiers")
            if isinstance(tiers, list):
                tier_dict = {}
                for item in tiers:
                    if isinstance(item, dict):
                        key = item.get("tier_name") or item.get("tier") or str(item)
                        val = item.get("points") or item.get("value") or 0
                        tier_dict[key] = val
                r["accelerated_tiers"] = tier_dict or None
            try:
                rewards_data = ExtractedRewardsData(**{k: v for k, v in r.items() if v is not None})
            except Exception as e:
                logger.warning(f"Rewards parse error: {e}")

        return ExtractedPage(
            page_number=page_num,
            page_type=data.get("page_type", "transaction"),
            skip=False,
            header=header,
            account_summary=account_summary,
            card_sections=card_sections if card_sections else None,
            fees_section=fees_section if fees_section else None,
            payments_section=payments_section if payments_section else None,
            rewards_data=rewards_data,
        )

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    def _retry_with_feedback(
        self, pdf_bytes: bytes, system_prompt: str,
        failed_response: str, issues: list,
    ) -> tuple:
        error_summary = "; ".join(i.get("detail", "")[:100] for i in issues[:2])
        content = self._build_message_content(pdf_bytes)
        content.append({
            "type": "text",
            "text": (
                f"Previous attempt had errors: {error_summary}. "
                "Return ONLY a valid JSON array, no other text whatsoever."
            )
        })
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=settings.extraction_max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
            )
            return self._parse_response(response.content[0].text)
        except Exception as e:
            logger.error(f"Retry also failed: {e}")
            return [], [{"type": "retry_failed", "detail": str(e)}]
