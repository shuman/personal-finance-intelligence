"""
Parser factory for detecting and selecting appropriate bank parser.
"""
from typing import Optional
import pdfplumber
from app.parsers.base import BaseParser
from app.parsers.amex import AmexParser


class ParserFactory:
    """
    Factory class to detect bank type and return appropriate parser.
    """

    # Register all available parsers
    _parsers = [
        AmexParser(),
        # Add more bank parsers here as they are implemented
        # ChaseParser(),
        # CitiParser(),
    ]

    @classmethod
    def get_parser(cls, pdf_path: str, bank_name: Optional[str] = None) -> BaseParser:
        """
        Get appropriate parser for the PDF.

        Args:
            pdf_path: Path to the PDF file
            bank_name: Optional bank name hint (e.g., "Amex", "Chase")

        Returns:
            Appropriate parser instance

        Raises:
            ValueError: If no suitable parser found
        """
        # If bank name provided, try to match directly
        if bank_name:
            bank_lower = bank_name.lower()
            if "amex" in bank_lower or "american express" in bank_lower:
                return AmexParser()

        # Auto-detect by reading first page
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if len(pdf.pages) > 0:
                    first_page_text = pdf.pages[0].extract_text() or ""

                    # Try each parser
                    for parser in cls._parsers:
                        if parser.can_parse(pdf_path, first_page_text):
                            return parser
        except Exception as e:
            raise ValueError(f"Error reading PDF for parser detection: {e}")

        # No parser found
        raise ValueError(f"No suitable parser found for this PDF. Supported banks: Amex")

    @classmethod
    def get_supported_banks(cls) -> list[str]:
        """Get list of supported bank names."""
        return ["American Express (Amex)"]
