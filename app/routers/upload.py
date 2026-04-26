"""
Upload router - handles PDF file uploads.
"""
import os
import re
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Body
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import User
from app.utils.db_errors import friendly_error
from app.routers.auth import get_current_user
from app.services import StatementService
from app.config import settings
from app.parsers import ParserFactory, AmexParser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])


# ---------------------------------------------------------------------------
# Server-side PDF validation (runs BEFORE any Claude API call)
# ---------------------------------------------------------------------------
_FINANCIAL_KEYWORDS = [
    "statement", "balance", "credit", "debit", "transaction",
    "payment", "card", "bank", "due", "period", "amount", "account",
]
_MIN_KEYWORD_MATCHES = 2


def _validate_pdf(pdf_path: str) -> Tuple[bool, str, int, bool]:
    """
    Validate that *pdf_path* is a readable, non-encrypted PDF that looks
    like a financial statement.

    Returns (valid, reason, pages, has_text).
    """
    import pdfplumber

    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception:
        return False, "The file is not a valid PDF. It may be corrupted.", 0, False

    pages = len(pdf.pages)
    if pages == 0:
        pdf.close()
        return False, "The PDF contains no pages.", 0, False

    # Extract text from first page (and second if available) to check content
    sample_text = ""
    for i in range(min(2, pages)):
        page_text = pdf.pages[i].extract_text() or ""
        sample_text += " " + page_text

    pdf.close()

    has_text = bool(sample_text.strip())
    if not has_text:
        return (
            False,
            "The PDF contains no readable text. It may be a scanned image or corrupted file.",
            pages,
            False,
        )

    # Check for financial-statement keywords (case-insensitive)
    text_lower = sample_text.lower()
    matches = sum(1 for kw in _FINANCIAL_KEYWORDS if re.search(rf"\b{kw}\b", text_lower))
    if matches < _MIN_KEYWORD_MATCHES:
        return (
            False,
            "This PDF doesn't appear to be a financial statement. "
            "Expected keywords like 'balance', 'transaction', 'payment' were not found.",
            pages,
            True,
        )

    return True, "", pages, True


def _serialize(obj: Any) -> Any:
    """Recursively convert dates and Decimals to JSON-serializable types."""
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    if hasattr(obj, '__float__'):
        return float(obj)
    return obj


def _serialize_dict(d: dict) -> dict:
    return {k: _serialize(v) for k, v in d.items()}


@router.post("/upload/preview")
async def preview_statement(
    file: UploadFile = File(..., description="Credit card statement PDF file"),
    password: str = Form(None, description="PDF password if protected"),
    bank_name: str = Form("Amex", description="Bank name (e.g., Amex)"),
    account_id: int = Form(None, description="Account ID (optional, for auto-matching)"),
    use_claude_vision: bool = Form(True, description="Use Claude AI Vision extraction"),
    extraction_model: Optional[str] = Form(None, description="Override extraction model (haiku/sonnet)"),
    use_extraction_cache: bool = Form(
        True,
        description="If true, reuse cached AI extraction for identical PDFs (same file hash)",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Parse PDF and return extracted data for preview before saving.

    Tries Claude Vision extraction first (if API key configured),
    falls back to regex parser. Runs CategoryEngine on all transactions.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    file_content = await file.read()
    if len(file_content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
        )

    try:
        file_hash = hashlib.sha256(file_content).hexdigest()

        temp_dir = os.path.join(settings.upload_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        safe_filename = os.path.basename(file.filename)
        if not safe_filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        temp_path = os.path.join(temp_dir, safe_filename)

        with open(temp_path, 'wb') as f:
            f.write(file_content)

        working_file = temp_path
        if password:
            parser = AmexParser()
            try:
                working_file = parser.decrypt_pdf(temp_path, password)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to decrypt PDF: {e}")

        # ------------------------------------------------------------------
        # Validate PDF before any AI processing
        # ------------------------------------------------------------------
        valid, reason, pdf_pages, pdf_has_text = _validate_pdf(working_file)
        if not valid:
            raise HTTPException(status_code=400, detail=reason)

        # ------------------------------------------------------------------
        # Extraction: Claude Vision → regex fallback
        # ------------------------------------------------------------------
        service = StatementService(db)
        extraction_method = "regex_fallback"
        extraction_meta: Dict[str, Any] = {}
        parsed_data = None

        if use_claude_vision and settings.anthropic_api_key:
            try:
                institution = await service._detect_institution(working_file, bank_name)
                parsed_data = await service._extract_with_claude_vision(
                    user_id=current_user.id,
                    pdf_path=working_file,
                    institution=institution,
                    file_hash=file_hash,
                    model=extraction_model,
                    use_extraction_cache=use_extraction_cache,
                )
                extraction_method = "claude_vision"
                ai_ext = parsed_data.pop("_ai_extraction", {})
                from_cache = ai_ext.get("from_cache", False)
                preflight_info = ai_ext.get("preflight") or {}
                extraction_meta = {
                    "model": ai_ext.get("model_used", "claude-haiku-4-5"),
                    "pages_processed": ai_ext.get("pages_processed", 0),
                    "pages_skipped": ai_ext.get("pages_skipped", 0),
                    "input_tokens": ai_ext.get("input_tokens", 0),
                    "output_tokens": ai_ext.get("output_tokens", 0),
                    "cost_usd": float(ai_ext.get("cost_usd", 0) or 0),
                    "confidence": float(ai_ext.get("extraction_confidence", 1.0) or 1.0),
                    "issues": ai_ext.get("issues_flagged", []),
                    "unmatched_cards": parsed_data.get("unmatched_cards", []),
                    "from_cache": from_cache,
                    "cached_at": ai_ext.get("cached_at"),
                    "original_cost_usd": float(ai_ext.get("original_cost_usd", 0) or 0),
                    "original_tokens": ai_ext.get("original_tokens", 0),
                    "preflight": {
                        "data_pages": preflight_info.get("data_pages", 0),
                        "skipped_pages": preflight_info.get("skipped_pages", 0),
                        "approx_rows": preflight_info.get("approx_rows", 0),
                        "page_summary": preflight_info.get("page_summary", []),
                        "issues": preflight_info.get("issues", []),
                    },
                }
                if from_cache:
                    logger.info(
                        f"Preview served from cache (file_hash={file_hash[:12]}…, "
                        f"saved ${extraction_meta['original_cost_usd']:.4f})"
                    )
                else:
                    logger.info(
                        f"Claude Vision preview: {extraction_meta['pages_processed']} pages, "
                        f"${extraction_meta['cost_usd']:.4f} USD"
                    )
            except Exception as e:
                logger.warning(f"Claude Vision failed in preview, falling back to regex: {e}")
                extraction_meta["fallback_reason"] = str(e)

        if parsed_data is None:
            parser = ParserFactory.get_parser(working_file, bank_name)
            parsed_data = parser.parse(working_file)
            extraction_method = "regex_fallback"

        # ------------------------------------------------------------------
        # Categorise every transaction (batch: rules first, then 1 Claude call)
        # ------------------------------------------------------------------
        from app.services.category_engine import CategoryEngine

        engine = CategoryEngine(db)

        txn_list = parsed_data.get("transactions", [])
        total_transactions = len(txn_list)

        logger.info(f"Starting batch categorization for {total_transactions} transactions")
        await engine.batch_categorize(txn_list, user_id=current_user.id)
        logger.info(f"Batch categorization complete for {total_transactions} transactions")

        # Categorization diagnostics
        cat_stats = getattr(engine, "last_batch_stats", {})

        # ------------------------------------------------------------------
        # Serialize for JSON response
        # ------------------------------------------------------------------
        metadata = _serialize_dict(parsed_data["metadata"])

        transactions = []
        for txn in parsed_data.get("transactions", []):
            transactions.append(_serialize_dict(txn))

        fees = [_serialize_dict(f) for f in parsed_data.get("fees", [])]
        interest_charges = [_serialize_dict(i) for i in parsed_data.get("interest_charges", [])]

        card_sections_meta = parsed_data.get("card_sections_meta", [])

        # Use Claude-extracted bank_name if available
        effective_bank_name = bank_name
        if parsed_data and parsed_data.get("metadata", {}).get("bank_name") not in (None, "Unknown", ""):
            effective_bank_name = parsed_data["metadata"]["bank_name"]

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "filename": file.filename,
                "file_hash": file_hash,
                "bank_name": effective_bank_name,
                "account_id": account_id,
                "extraction_method": extraction_method,
                "extraction_meta": extraction_meta,
                "card_sections_meta": card_sections_meta,
                "metadata": metadata,
                "transactions": transactions,
                "categorization_stats": cat_stats,
                "fees": fees,
                "interest_charges": interest_charges,
                "temp_path": temp_path,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing file: {str(e)}")


@router.post("/upload/save")
async def save_statement(
    data: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save the previewed and edited statement data to database.

    Expects the edited data from preview endpoint.
    """
    try:
        service = StatementService(db)
        result = await service.save_previewed_data(data, user_id=current_user.id)

        # Clean up temp file — validate path is within allowed temp directory
        _allowed_temp = os.path.realpath(os.path.join(settings.upload_dir, "temp"))
        temp_path = data.get("temp_path")
        if temp_path:
            temp_path = os.path.realpath(temp_path)
            if not temp_path.startswith(_allowed_temp + os.sep):
                temp_path = None
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                # Also remove decrypted temp if exists
                decrypted_path = temp_path.replace('.pdf', '_decrypted.pdf')
                if os.path.exists(decrypted_path):
                    os.remove(decrypted_path)
            except:
                pass

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                **result
            }
        )

    except ValueError as e:
        # Clean up temp file on validation error
        _allowed_temp = os.path.realpath(os.path.join(settings.upload_dir, "temp"))
        temp_path = data.get("temp_path")
        if temp_path:
            temp_path = os.path.realpath(temp_path)
            if not temp_path.startswith(_allowed_temp + os.sep):
                temp_path = None
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                decrypted_path = temp_path.replace('.pdf', '_decrypted.pdf')
                if os.path.exists(decrypted_path):
                    os.remove(decrypted_path)
            except:
                pass
        raise HTTPException(status_code=400, detail=str(e))
    except IntegrityError as e:
        # Handle database constraint violations with user-friendly messages
        _allowed_temp = os.path.realpath(os.path.join(settings.upload_dir, "temp"))
        temp_path = data.get("temp_path")
        if temp_path:
            temp_path = os.path.realpath(temp_path)
            if not temp_path.startswith(_allowed_temp + os.sep):
                temp_path = None
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                decrypted_path = temp_path.replace('.pdf', '_decrypted.pdf')
                if os.path.exists(decrypted_path):
                    os.remove(decrypted_path)
            except:
                pass
        raise HTTPException(status_code=400, detail=friendly_error(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error saving data. Please try again.")


@router.post("/upload")
async def upload_statement(
    file: UploadFile = File(..., description="Credit card statement PDF file"),
    password: str = Form(None, description="PDF password if protected"),
    bank_name: str = Form("Amex", description="Bank name (e.g., Amex)"),
    account_id: int = Form(None, description="Account ID (optional)"),
    use_extraction_cache: bool = Form(
        True,
        description="If true, reuse cached AI extraction for identical PDFs",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload and process a credit card statement PDF (direct save without preview).

    - **file**: PDF file to upload
    - **password**: Optional password for encrypted PDFs
    - **bank_name**: Bank name (currently supports: Amex)

    Returns processing summary with statement ID and statistics.
    """
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Check file size
    file_content = await file.read()
    if len(file_content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.max_file_size_mb}MB"
        )

    safe_filename = os.path.basename(file.filename)
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Process statement
    service = StatementService(db)

    try:
        result = await service.process_statement(
            file_content=file_content,
            filename=safe_filename,
            user_id=current_user.id,
            password=password,
            bank_name=bank_name,
            account_id=account_id,
            use_extraction_cache=use_extraction_cache,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                **result
            }
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IntegrityError as e:
        raise HTTPException(status_code=400, detail=friendly_error(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error processing file. Please try again.")
