"""
Upload router - handles PDF file uploads.
"""
import os
import hashlib
import json
from typing import Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Body
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.services import StatementService
from app.config import settings
from app.parsers import ParserFactory, AmexParser

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload/preview")
async def preview_statement(
    file: UploadFile = File(..., description="Credit card statement PDF file"),
    password: str = Form(None, description="PDF password if protected"),
    bank_name: str = Form("Amex", description="Bank name (e.g., Amex)"),
):
    """
    Parse PDF and return extracted data for preview before saving.

    - **file**: PDF file to upload
    - **password**: Optional password for encrypted PDFs
    - **bank_name**: Bank name (currently supports: Amex)

    Returns parsed data for preview and editing.
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

    try:
        # Calculate file hash
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Save temp file
        temp_dir = os.path.join(settings.upload_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, file.filename)

        with open(temp_path, 'wb') as f:
            f.write(file_content)

        # Decrypt if needed
        working_file = temp_path
        if password:
            parser = AmexParser()
            try:
                working_file = parser.decrypt_pdf(temp_path, password)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to decrypt PDF: {e}")

        # Parse PDF
        parser = ParserFactory.get_parser(working_file, bank_name)
        parsed_data = parser.parse(working_file)

        # Convert dates and decimals to strings for JSON serialization
        metadata = parsed_data["metadata"]
        for key, value in metadata.items():
            if hasattr(value, 'isoformat'):
                metadata[key] = value.isoformat()
            elif hasattr(value, '__float__'):
                metadata[key] = float(value)

        transactions = parsed_data["transactions"]
        for txn in transactions:
            for key, value in txn.items():
                if hasattr(value, 'isoformat'):
                    txn[key] = value.isoformat()
                elif hasattr(value, '__float__'):
                    txn[key] = float(value)

        fees = parsed_data["fees"]
        for fee in fees:
            for key, value in fee.items():
                if hasattr(value, '__float__'):
                    fee[key] = float(value)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "filename": file.filename,
                "file_hash": file_hash,
                "bank_name": bank_name,
                "password": password,
                "metadata": metadata,
                "transactions": transactions,
                "fees": fees,
                "interest_charges": parsed_data["interest_charges"],
                "temp_path": temp_path
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing file: {str(e)}")


@router.post("/upload/save")
async def save_statement(
    data: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Save the previewed and edited statement data to database.

    Expects the edited data from preview endpoint.
    """
    try:
        service = StatementService(db)
        result = await service.save_previewed_data(data)

        # Clean up temp file
        temp_path = data.get("temp_path")
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
        temp_path = data.get("temp_path")
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
        # Handle database constraint violations with detailed error
        error_msg = str(e.orig) if hasattr(e, 'orig') else str(e)
        # Extract the field name from NOT NULL constraint error
        if "NOT NULL constraint failed:" in error_msg:
            field = error_msg.split("NOT NULL constraint failed:")[-1].strip()
            raise HTTPException(
                status_code=400,
                detail=f"Required field is missing: {field}\n\nThis field must be present in the PDF or provided in the form."
            )
        elif "UNIQUE constraint failed: transactions" in error_msg:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Duplicate transactions detected. This file contains transactions with identical:\n"
                    "  - Date\n"
                    "  - Description\n"
                    "  - Amount\n\n"
                    "This usually means:\n"
                    "  1. The same file was uploaded twice, OR\n"
                    "  2. The statement has multiple identical transactions (rare)\n\n"
                    "Check the statement list to see if this file was already processed."
                )
            )
        raise HTTPException(status_code=400, detail=f"Database constraint error: {error_msg}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving data: {str(e)}")


@router.post("/upload")
async def upload_statement(
    file: UploadFile = File(..., description="Credit card statement PDF file"),
    password: str = Form(None, description="PDF password if protected"),
    bank_name: str = Form("Amex", description="Bank name (e.g., Amex)"),
    db: AsyncSession = Depends(get_db)
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

    # Process statement
    service = StatementService(db)

    try:
        result = await service.process_statement(
            file_content=file_content,
            filename=file.filename,
            password=password,
            bank_name=bank_name
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
