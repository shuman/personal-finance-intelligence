"""
American Express (Amex) credit card statement parser.
Handles CBL Amex Gold and other Amex statement formats.
"""
import re
import pikepdf
import pdfplumber
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional
from app.parsers.base import BaseParser
from app.utils.categorization import (
    clean_merchant_name,
    categorize_transaction,
    extract_merchant_info,
    is_recurring_transaction,
    detect_transaction_type
)


class AmexParser(BaseParser):
    """
    Parser for American Express credit card statements.

    Handles:
    - Password-protected PDFs
    - Statement metadata extraction
    - Transaction table parsing
    - Fee and interest extraction
    - Rewards/points tracking
    """

    def __init__(self):
        self.decrypted_pdf_path: Optional[str] = None

    def can_parse(self, pdf_path: str, text_sample: str) -> bool:
        """
        Check if this is an Amex statement.

        Looks for Amex identifiers in the PDF text.
        """
        identifiers = [
            "american express",
            "amex",
            "city bank limited",
            "cbl",
        ]

        text_lower = text_sample.lower()
        return any(identifier in text_lower for identifier in identifiers)

    def decrypt_pdf(self, pdf_path: str, password: Optional[str]) -> str:
        """
        Decrypt password-protected PDF.

        Args:
            pdf_path: Path to encrypted PDF
            password: PDF password (if protected)

        Returns:
            Path to decrypted PDF
        """
        try:
            # Try to open with password
            with pikepdf.open(pdf_path, password=password or "") as pdf:
                # Save decrypted version
                temp_path = pdf_path.replace('.pdf', '_decrypted.pdf')
                if temp_path == pdf_path:
                    temp_path = pdf_path + '_decrypted.pdf'

                pdf.save(temp_path)
                self.decrypted_pdf_path = temp_path
                return temp_path
        except Exception as e:
            # If no password needed or wrong password
            raise ValueError(f"Could not decrypt PDF: {e}")

    def _extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract all text from PDF."""
        full_text = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text.append(text)
        return "\n".join(full_text)

    def _extract_pattern(self, text: str, pattern: str, group: int = 1) -> Optional[str]:
        """Extract first match of regex pattern."""
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        return match.group(group).strip() if match else None

    def _extract_currency(self, text: str, pattern: str) -> Optional[Decimal]:
        """Extract currency value and convert to Decimal."""
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value_str = match.group(1).replace(',', '').replace('₹', '').replace('Rs.', '').replace('BDT', '').replace('৳', '').strip()
            try:
                return Decimal(value_str)
            except:
                return None
        return None

    def _parse_decimal(self, value_str: str) -> Optional[Decimal]:
        """Parse string to Decimal, removing commas and currency symbols."""
        try:
            cleaned = value_str.replace(',', '').replace('₹', '').replace('Rs.', '').replace('BDT', '').replace('৳', '').strip()
            return Decimal(cleaned)
        except:
            return None

    def _extract_date(self, text: str, pattern: str) -> Optional[datetime]:
        """Extract and parse date."""
        date_str = self._extract_pattern(text, pattern)
        if not date_str:
            return None

        # Try multiple date formats
        formats = [
            '%d %b, %Y',  # 23 Nov, 2025
            '%d %B, %Y',  # 23 November, 2025
            '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y',
            '%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y',
            '%d-%b-%Y', '%d-%B-%Y',
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except:
                continue

        return None

    def extract_statement_metadata(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract statement-level metadata from Amex PDF.

        Extracts:
        - Account information
        - Statement dates
        - Financial summary
        - Credit information
        - Rewards data
        """
        # Decrypt if needed (password will be handled by service layer)
        working_pdf = pdf_path

        # Extract text
        full_text = self._extract_text_from_pdf(working_pdf)

        # Extract account information from filename if available
        # Pattern: CBL_AMEX_Gold_<account>_<statement>_<date>.pdf
        filename = pdf_path.split('/')[-1]
        account_match = re.search(r'_(\d{10,})_', filename)
        account_number = account_match.group(1) if account_match else None

        # If not in filename, try to extract CLIENT ID from PDF
        if not account_number:
            client_id = self._extract_pattern(full_text, r'CLIENT\s+ID\s*:\s*(\d+)')
            account_number = client_id if client_id else None

        if not account_number:
            account_number = self._extract_pattern(
                full_text,
                r'Account\s+(?:Number|No\.?)[:\s]+(\d+)'
            )

        metadata = {
            # Account Information
            "account_number": account_number or "UNKNOWN",
            "card_type": self._extract_pattern(full_text, r'(Gold|Platinum|Green|Blue|Centurion)\s+Card') or "American Express Gold",
            "cardholder_name": self._extract_pattern(full_text, r'(?:Card\s+Member|Name)[:\s]+([A-Z\s]+)'),
            "member_since": None,  # Extract year if available

            # Statement Dates
            "statement_date": None,
            "statement_period_from": None,
            "statement_period_to": None,
            "payment_due_date": None,
            "statement_number": self._extract_pattern(full_text, r'Statement\s+(?:Number|No\.?)[:\s]+(\d+)'),
            "billing_cycle": None,

            # Financial Summary - support both INR (₹) and BDT (৳) symbols
            "previous_balance": self._extract_currency(full_text, r'Previous\s+Balance\s+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "payments_credits": self._extract_currency(full_text, r'Payments?\s*(?:/|&)?\s*Credits?[:\s]+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "purchases": self._extract_currency(full_text, r'Purchases[:\s]+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "cash_advances": self._extract_currency(full_text, r'Cash\s+Advances?[:\s]+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "fees_charged": self._extract_currency(full_text, r'Fees?\s+Charged[:\s]+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "interest_charged": self._extract_currency(full_text, r'Interest\s+Charged[:\s]+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "adjustments": self._extract_currency(full_text, r'Adjustments?[:\s]+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),
            "new_balance": self._extract_currency(full_text, r'New\s+Balance\s+(?:Rs\.?|₹|BDT|৳)?\s*([\d,]+\.?\d*)'),

            # Payment Information
            "total_amount_due": None,  # Will extract from structured format below
            "minimum_payment_due": None,  # Will extract from structured format below

            # Credit Information
            "credit_limit": None,  # Will extract from structured format below
            "available_credit": None,  # Will extract from structured format below
            "cash_advance_limit": None,  # Will extract from structured format below,

            # Rewards/Points
            "rewards_opening": None,
            "rewards_earned": None,
            "rewards_redeemed": None,
            "rewards_closing": None,
            "rewards_value_inr": None,

            # Currency - detect from statement
            "currency": "BDT",  # Default to BDT for Bangladesh statements
        }

        # Detect currency from first line (376948*****9844 BDT 93,171.64)
        currency_match = re.search(r'376948\*+\d+\s+(BDT|INR|USD)\s+[\d,]+\.?\d*', full_text)
        if currency_match:
            metadata["currency"] = currency_match.group(1)

        # Extract structured line: BDT 400,000.00 BDT 305,838.48 BDT 200,000.00 28908.00 BDT 2,824.85
        # Format: [Currency] [Credit Limit] [Currency] [Available Credit] [Currency] [Cash Limit] [Rewards] [Currency] [Min Payment]
        structured_pattern = r'([A-Z]{3})\s+([\d,]+\.?\d*)\s+([A-Z]{3})\s+([\d,]+\.?\d*)\s+([A-Z]{3})\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([A-Z]{3})\s+([\d,]+\.?\d*)'
        structured_match = re.search(structured_pattern, full_text)
        if structured_match:
            metadata["credit_limit"] = self._parse_decimal(structured_match.group(2))
            metadata["available_credit"] = self._parse_decimal(structured_match.group(4))
            metadata["cash_advance_limit"] = self._parse_decimal(structured_match.group(6))
            metadata["rewards_closing"] = int(float(structured_match.group(7).replace(',', '')))
            metadata["minimum_payment_due"] = self._parse_decimal(structured_match.group(9))

        # Extract total amount due from line like: 376948*****9844 BDT 93,171.64
        total_due_match = re.search(r'376948\*+\d+\s+(?:BDT|INR|USD)\s+([\d,]+\.?\d*)', full_text)
        if total_due_match:
            metadata["total_amount_due"] = self._parse_decimal(total_due_match.group(1))

        # Extract dates - looking for format "23 Nov, 2025 08 Dec, 2025" at top of page
        # First date is statement date, second is payment due date
        date_pattern = r'(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})'
        date_matches = re.findall(date_pattern, full_text)

        if len(date_matches) >= 2:
            # First match is statement date
            statement_date = self._extract_date(full_text, f'({date_matches[0]})')
            if statement_date:
                metadata["statement_date"] = statement_date.date()

            # Second match is payment due date
            payment_due = self._extract_date(full_text, f'({date_matches[1]})')
            if payment_due:
                metadata["payment_due_date"] = payment_due.date()

        # Try to parse from filename if not found: ..._23112025.pdf (DDMMYYYY)
        if not metadata["statement_date"]:
            date_match = re.search(r'_(\d{8})\.pdf$', filename)
            if date_match:
                date_str = date_match.group(1)
                try:
                    metadata["statement_date"] = datetime.strptime(date_str, '%d%m%Y').date()
                except:
                    pass

        # Extract period - looking for "24 Oct, 2025 to 23 Nov, 2025" at bottom
        period_pattern = r'(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})\s+to\s+(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})'
        period_match = re.search(period_pattern, full_text)
        if period_match:
            period_from = self._extract_date(full_text, f'({period_match.group(1)})')
            period_to = self._extract_date(full_text, f'({period_match.group(2)})')
            if period_from:
                metadata["statement_period_from"] = period_from.date()
            if period_to:
                metadata["statement_period_to"] = period_to.date()

        # Fallback: if period dates not found, try to derive from statement_date
        # Use statement_date as period_to and calculate period_from as 30 days before
        if not metadata["statement_period_from"] or not metadata["statement_period_to"]:
            if metadata["statement_date"]:
                from datetime import timedelta
                metadata["statement_period_to"] = metadata["statement_date"]
                # Assume 30-day billing cycle as fallback
                metadata["statement_period_from"] = metadata["statement_date"] - timedelta(days=30)

        # Calculate credit utilization if possible
        if metadata["credit_limit"] and metadata["new_balance"]:
            utilization = (metadata["new_balance"] / metadata["credit_limit"]) * 100
            metadata["credit_utilization_pct"] = round(utilization, 2)

        return metadata

    def extract_transactions(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract all transactions from Amex statement.

        Transactions are typically in a table format with columns:
        - Date
        - Description
        - Amount

        Some statements may have additional columns like posting date, reference, etc.
        """
        transactions = []

        working_pdf = pdf_path

        with pdfplumber.open(working_pdf) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                # Extract tables
                tables = page.extract_tables()

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Try to identify transaction table
                    # Look for headers containing keywords
                    header_row = table[0]
                    header_text = ' '.join([str(cell or '') for cell in header_row]).lower()

                    # Check if this looks like a transaction table
                    is_txn_table = any(keyword in header_text for keyword in
                                      ['date', 'description', 'amount', 'transaction'])

                    if not is_txn_table:
                        # Try first data row as header
                        if len(table) > 1:
                            header_row = table[1]
                            header_text = ' '.join([str(cell or '') for cell in header_row]).lower()
                            is_txn_table = any(keyword in header_text for keyword in
                                              ['date', 'description', 'amount', 'transaction'])

                    if not is_txn_table:
                        continue

                    # Process transaction rows
                    for row_idx, row in enumerate(table[1:], 1):
                        if not row or not any(row):
                            continue

                        # Skip header rows
                        row_text = ' '.join([str(cell or '') for cell in row]).lower()
                        if any(keyword in row_text for keyword in ['date', 'description', 'amount', 'transaction']):
                            continue

                        # Parse transaction
                        txn = self._parse_transaction_row(row)
                        if txn:
                            transactions.append(txn)

        # If no transactions found in tables, try parsing from text
        if not transactions:
            transactions = self._extract_transactions_from_text(working_pdf)

        return transactions

    def _parse_transaction_row(self, row: List) -> Optional[Dict[str, Any]]:
        """
        Parse a single transaction row.

        Typical row format:
        [date, description, amount]
        or
        [date, posting_date, description, amount]
        """
        if not row or len(row) < 3:
            return None

        transaction = {
            "transaction_date": None,
            "posting_date": None,
            "description_raw": "",
            "amount": None,
            "currency": "INR",
            "transaction_type": "PURCHASE",
            "debit_credit": "D",
        }

        # Try to identify columns
        date_col = None
        desc_col = None
        amount_col = None

        for i, cell in enumerate(row):
            cell_str = str(cell or '').strip()

            if not cell_str:
                continue

            # Date detection
            if self._is_date_string(cell_str) and transaction["transaction_date"] is None:
                parsed_date = self._parse_date_string(cell_str)
                if parsed_date:
                    transaction["transaction_date"] = parsed_date.date()
                    date_col = i
                    continue

            # Amount detection (look for currency or decimal pattern)
            if self._is_amount_string(cell_str) and transaction["amount"] is None:
                amount = self._parse_amount(cell_str)
                if amount is not None:
                    transaction["amount"] = abs(amount)
                    # Negative amounts or parentheses indicate debits
                    if amount < 0 or '(' in cell_str:
                        transaction["debit_credit"] = "D"
                    else:
                        transaction["debit_credit"] = "C"
                        transaction["transaction_type"] = "PAYMENT"
                    amount_col = i
                    continue

            # Description (everything else)
            if desc_col is None or i != date_col and i != amount_col:
                if transaction["description_raw"]:
                    transaction["description_raw"] += " " + cell_str
                else:
                    transaction["description_raw"] = cell_str
                desc_col = i

        # Validate transaction
        if not transaction["transaction_date"] or not transaction["description_raw"] or transaction["amount"] is None:
            return None

        # Clean description
        transaction["description_raw"] = transaction["description_raw"].strip()

        # Extract merchant info and categorize
        merchant_info = extract_merchant_info(transaction["description_raw"])
        transaction["merchant_name"] = merchant_info["merchant_name"]
        transaction["merchant_city"] = merchant_info["city"]
        transaction["merchant_country"] = merchant_info["country"]

        # Categorize
        transaction["merchant_category"] = categorize_transaction(
            transaction["description_raw"],
            transaction["merchant_name"]
        )

        # Detect transaction type
        transaction["transaction_type"] = detect_transaction_type(
            transaction["description_raw"],
            float(transaction["amount"]) if transaction["debit_credit"] == "D" else -float(transaction["amount"])
        )

        # Check if recurring
        transaction["is_recurring"] = is_recurring_transaction(transaction["description_raw"])

        # Check if international
        transaction["is_international"] = merchant_info["country"] != "IN"

        return transaction

    def _is_date_string(self, text: str) -> bool:
        """Check if string looks like a date."""
        date_patterns = [
            r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$',
            r'^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}$',
            r'^\d{1,2}\s+[A-Za-z]{3},\s+\d{4}$',  # 23 Nov, 2025
        ]
        return any(re.match(pattern, text.strip()) for pattern in date_patterns)

    def _parse_date_string(self, text: str) -> Optional[datetime]:
        """Parse date string."""
        formats = [
            '%d %b, %Y',  # 23 Nov, 2025
            '%d %B, %Y',  # 23 November, 2025
            '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y',
            '%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y',
        ]

        for fmt in formats:
            try:
                return datetime.strptime(text.strip(), fmt)
            except:
                continue
        return None

    def _is_amount_string(self, text: str) -> bool:
        """Check if string looks like an amount."""
        # Remove currency symbols
        cleaned = text.replace('৳', '').replace('BDT', '').replace(',', '').strip()
        # Check if it's a number (possibly negative or in parentheses)
        return bool(re.match(r'^[\(\-]?\d+\.?\d*\)?$', cleaned))

    def _parse_amount(self, text: str) -> Optional[Decimal]:
        """Parse amount string to Decimal."""
        # Remove currency symbols and commas
        cleaned = text.replace('৳', '').replace('BDT', '').replace(',', '').strip()

        # Check for parentheses (negative)
        is_negative = '(' in text or ')' in text
        cleaned = cleaned.replace('(', '').replace(')', '')

        # Check for explicit negative
        if cleaned.startswith('-'):
            is_negative = True
            cleaned = cleaned[1:]

        try:
            amount = Decimal(cleaned)
            return -amount if is_negative else amount
        except:
            return None

    def _extract_transactions_from_text(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Fallback method to extract transactions from plain text.
        Used when table extraction fails.

        Format: DD MMM, YYYY Purchase,description,location,country [FOREIGN_CURR AMT] LOCAL_CURR AMT
        Example: 27 Oct, 2025 Purchase,google,google one,g.co/helppay#,united states USD 2.11 BDT 261.11
        """
        transactions = []
        full_text = self._extract_text_from_pdf(pdf_path)

        # Pattern to match transaction lines with optional CR (credit) marker
        # Date, then description with commas, optional foreign currency with optional CR, then local amount with optional CR
        # Example: 28 Aug, 2025 Merchandize return claude.ai subscription,san francisco,united states USD 20.00 CR BDT 2,470.00 CR
        pattern = r'(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})\s+((?:Purchase|Payment|Refund|VAT|Cash|Merchandize\s+return)[^\n]+?)(?:([A-Z]{3})\s+([\d,]+\.?\d*)\s*(CR)?)?(?:\s+([A-Z]{3})\s+([\d,]+\.?\d*)\s*(CR)?)'

        for match in re.finditer(pattern, full_text, re.MULTILINE):
            try:
                date_str = match.group(1)
                description = match.group(2).strip()
                foreign_currency = match.group(3)
                foreign_amount_str = match.group(4)
                foreign_is_credit = match.group(5) == 'CR'
                local_currency = match.group(6)
                local_amount_str = match.group(7)
                local_is_credit = match.group(8) == 'CR'

                # Parse date
                transaction_date = None
                try:
                    transaction_date = datetime.strptime(date_str, '%d %b, %Y').date()
                except:
                    continue

                # Parse amounts
                if not local_amount_str:
                    continue

                try:
                    local_amount = Decimal(local_amount_str.replace(',', ''))
                except:
                    continue

                foreign_amount = None
                if foreign_amount_str:
                    try:
                        foreign_amount = Decimal(foreign_amount_str.replace(',', ''))
                    except:
                        pass

                # Determine transaction type and debit/credit based on CR marker or description
                is_credit = local_is_credit or description.lower().startswith(('payment', 'refund', 'credit', 'merchandize return'))
                txn_type = detect_transaction_type(description, -float(local_amount) if is_credit else float(local_amount))

                # Extract merchant info
                merchant_info = extract_merchant_info(description)

                # Build transaction
                transaction = {
                    "transaction_date": transaction_date,
                    "posting_date": None,
                    "description_raw": description,
                    "description_cleaned": clean_merchant_name(description),
                    "merchant_name": merchant_info["merchant_name"],
                    "merchant_city": merchant_info["city"],
                    "merchant_state": merchant_info["state"],
                    "merchant_country": merchant_info["country"],
                    "amount": local_amount,
                    "currency": local_currency or "BDT",
                    "foreign_amount": foreign_amount,
                    "foreign_currency": foreign_currency,
                    "exchange_rate": None,
                    "transaction_type": txn_type,
                    "debit_credit": "C" if is_credit else "D",
                    "reference_number": None,
                    "authorization_code": None,
                    "card_last_four": None,
                    "is_international": foreign_currency is not None or merchant_info["country"] not in ["BD", "IN", ""],
                    "is_emi": False,
                    "emi_tenure": None,
                    "emi_month": None,
                    "rewards_earned": 0,
                    "rewards_multiplier": None,
                    "category_manual": None,
                    "tags": None,
                    "notes": None,
                    "is_recurring": is_recurring_transaction(description),
                    "recurring_frequency": None,
                    "receipt_url": None,
                    "is_business": False,
                    "tax_deductible": False,
                }

                # Categorize
                transaction["merchant_category"] = categorize_transaction(
                    description,
                    merchant_info["merchant_name"]
                )

                # Calculate exchange rate if foreign transaction
                if foreign_amount and foreign_amount > 0:
                    transaction["exchange_rate"] = round(local_amount / foreign_amount, 6)

                transactions.append(transaction)

            except Exception as e:
                # Skip invalid transactions
                continue

        return transactions

    def extract_fees(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract fee charges from Amex statement.
        """
        fees = []
        working_pdf = pdf_path

        full_text = self._extract_text_from_pdf(working_pdf)

        # Common fee patterns
        fee_patterns = {
            "LATE_PAYMENT_FEE": r'Late\s+(?:Payment\s+)?Fee[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
            "ANNUAL_FEE": r'Annual\s+(?:Membership\s+)?Fee[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
            "OVER_LIMIT_FEE": r'Over\s*[- ]?Limit\s+Fee[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
            "FOREIGN_TRANSACTION_FEE": r'Foreign\s+Transaction\s+Fee[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
            "CASH_ADVANCE_FEE": r'Cash\s+Advance\s+Fee[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
            "SERVICE_FEE": r'Service\s+Fee[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
            "GST": r'GST[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)',
        }

        for fee_type, pattern in fee_patterns.items():
            amount = self._extract_currency(full_text, pattern)
            if amount:
                fees.append({
                    "fee_type": fee_type,
                    "fee_description": fee_type.replace('_', ' ').title(),
                    "amount": amount,
                    "currency": "INR",
                    "gst_rate": Decimal("18.00") if fee_type != "GST" else None,
                    "waived": False,
                })

        return fees

    def extract_interest_charges(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract interest calculation details.
        """
        interest_charges = []
        working_pdf = pdf_path

        full_text = self._extract_text_from_pdf(working_pdf)

        # Extract APR and interest amounts
        purchase_apr = self._extract_pattern(full_text, r'Purchase\s+APR[:\s]+([\d.]+)%?')
        cash_apr = self._extract_pattern(full_text, r'Cash\s+Advance\s+APR[:\s]+([\d.]+)%?')

        purchase_interest = self._extract_currency(full_text, r'Interest\s+on\s+Purchases?[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)')
        cash_interest = self._extract_currency(full_text, r'Interest\s+on\s+Cash\s+Advances?[:\s]+(?:Rs\.?|৳)?\s*([\d,]+\.?\d*)')

        if purchase_interest:
            interest_charges.append({
                "interest_type": "PURCHASE_INTEREST",
                "apr": Decimal(purchase_apr) if purchase_apr else None,
                "interest_charged": purchase_interest,
                "currency": "INR",
                "calculation_method": "AVERAGE_DAILY_BALANCE",
            })

        if cash_interest:
            interest_charges.append({
                "interest_type": "CASH_ADVANCE_INTEREST",
                "apr": Decimal(cash_apr) if cash_apr else None,
                "interest_charged": cash_interest,
                "currency": "INR",
                "calculation_method": "AVERAGE_DAILY_BALANCE",
            })

        return interest_charges
