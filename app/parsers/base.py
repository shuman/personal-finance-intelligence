"""
Base parser abstract class.
All bank-specific parsers must extend this class.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from datetime import date


class BaseParser(ABC):
    """
    Abstract base class for credit card statement parsers.

    Each bank-specific parser must implement these methods to handle
    their unique PDF formats and data structures.
    """

    @abstractmethod
    def can_parse(self, pdf_path: str, text_sample: str) -> bool:
        """
        Determine if this parser can handle the given PDF.

        Args:
            pdf_path: Path to the PDF file
            text_sample: Sample text from first page of PDF

        Returns:
            True if this parser can handle the PDF, False otherwise

        Example:
            return "AMERICAN EXPRESS" in text_sample.upper()
        """
        pass

    @abstractmethod
    def extract_statement_metadata(self, pdf_path: str) -> Dict[str, Any]:
        """
        Extract statement-level metadata.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Dictionary containing statement metadata:
            {
                "account_number": str,
                "card_type": str,
                "cardholder_name": str,
                "statement_date": date,
                "statement_period_from": date,
                "statement_period_to": date,
                "payment_due_date": date,
                "previous_balance": Decimal,
                "new_balance": Decimal,
                "total_amount_due": Decimal,
                "minimum_payment_due": Decimal,
                "credit_limit": Decimal,
                "available_credit": Decimal,
                "rewards_opening": int,
                "rewards_earned": int,
                "rewards_closing": int,
                ... (other fields as available)
            }
        """
        pass

    @abstractmethod
    def extract_transactions(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract all transactions from the statement.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of transaction dictionaries:
            [
                {
                    "transaction_date": date,
                    "posting_date": date (optional),
                    "description_raw": str,
                    "amount": Decimal,
                    "transaction_type": str,
                    "merchant_name": str (optional),
                    "merchant_city": str (optional),
                    ... (other fields as available)
                },
                ...
            ]
        """
        pass

    def extract_fees(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract fee charges (optional - override if fees are separate).

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of fee dictionaries
        """
        return []

    def extract_interest_charges(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract interest charges (optional - override if available).

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of interest charge dictionaries
        """
        return []

    def extract_category_summary(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract category-wise spending summary (optional).

        Args:
            pdf_path: Path to the PDF file

        Returns:
            List of category summary dictionaries
        """
        return []

    def parse(self, pdf_path: str) -> Dict[str, Any]:
        """
        Main parse method - orchestrates all extraction.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Complete parsed data including metadata, transactions, fees, etc.
        """
        return {
            "metadata": self.extract_statement_metadata(pdf_path),
            "transactions": self.extract_transactions(pdf_path),
            "fees": self.extract_fees(pdf_path),
            "interest_charges": self.extract_interest_charges(pdf_path),
            "category_summary": self.extract_category_summary(pdf_path),
        }
