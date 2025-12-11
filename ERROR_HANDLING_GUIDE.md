# Error Handling Guide

## Overview

This application now has comprehensive error handling to help you understand exactly what's wrong when data cannot be saved to the database.

## Common Errors and Solutions

### 1. Missing Required Fields

**Error Message:**
```
Missing required fields in statement data:
  - Statement Period From (statement_period_from)
  - Statement Period To (statement_period_to)

These fields are required by the database and must be extracted from the PDF.
Please check if the PDF format matches the expected format or if these dates are present in the PDF.
```

**What This Means:**
The PDF parser couldn't find some required date fields in your statement PDF.

**Required Fields:**
- `statement_date` - The statement closing date
- `statement_period_from` - Start date of billing period
- `statement_period_to` - End date of billing period

**Solutions:**

1. **Check PDF Format**: Make sure your PDF contains these dates clearly visible

2. **Statement Period Format**: The parser looks for patterns like:
   - "24 Oct, 2025 to 23 Nov, 2025"
   - "From: 24/10/2025 To: 23/11/2025"

3. **Fallback Mechanism**: If period dates aren't found, the system will:
   - Use `statement_date` as `period_to`
   - Calculate `period_from` as 30 days before statement_date

4. **Manual Override in Preview**: You can manually enter these dates in the preview page before saving

### 2. Database Constraint Errors

**Error Message:**
```
Required field is missing: statements.statement_period_from

This field must be present in the PDF or provided in the form.
```

**What This Means:**
A required database field is NULL (empty) when trying to save.

**Solution:**
Use the **Preview Mode** to check and fill in missing fields before saving:
1. Upload your PDF
2. Review the extracted data in preview page
3. Yellow-highlighted fields are missing - fill them in
4. Click "Save to Database"

### 3. Duplicate Transaction Errors

**Error Message:**
```
Duplicate transactions detected. This file contains transactions with identical:
  - Date
  - Description
  - Amount

This usually means:
  1. The same file was uploaded twice, OR
  2. The statement has multiple identical transactions (rare)

Check the statement list to see if this file was already processed.
```

**What This Means:**
The database found transactions with the exact same date, description, and amount within the same statement.

**Why This Happens:**
- **Most Common**: You're uploading the same file again
- **Rare**: The statement genuinely has duplicate transactions (e.g., two $10 coffee purchases on same day at same place)

**Solutions:**

1. **Check Statement List**:
   - Go to the statements page
   - Look for a statement with the same date/filename
   - If found, you don't need to upload again

2. **If Genuinely New File**:
   - The system will skip duplicate transactions automatically
   - Only unique transactions will be saved
   - You'll see: "X duplicate transaction(s) were skipped"

3. **All Transactions Duplicate**:
   - If ALL transactions are duplicates, upload is rejected
   - This prevents duplicate statements in the database

## How Error Handling Works

### 1. Validation Before Database Insert

The system validates all required fields **before** attempting to save to database:

```python
# Checks performed:
- statement_date must not be None
- statement_period_from must not be None
- statement_period_to must not be None
```

If any field is missing, you get a clear error message listing **all** missing fields.

### 2. Detailed Error Messages

Errors now show:
- **Field Name**: The technical database field name
- **Field Label**: Human-readable name
- **Context**: Where the issue occurred
- **Solution**: What to do to fix it

### 3. Graceful Cleanup

When errors occur:
- Temporary files are cleaned up
- No partial data is saved to database
- Original file is preserved (if already saved)

## Developer Notes

### Adding New Required Fields

If you add a new NOT NULL field to the database:

1. Update `_validate_required_fields()` in `app/services/statement_service.py`:
```python
required_fields = {
    "statement_date": "Statement Date",
    "statement_period_from": "Statement Period From",
    "statement_period_to": "Statement Period To",
    "your_new_field": "Your Field Label",  # Add this
}
```

2. Update the parser to extract this field from PDF

3. Add to preview template if user should be able to edit it

### Error Types

**ValueError**:
- Missing required fields
- Invalid data format
- Duplicate file detection

**IntegrityError**:
- Database constraint violations
- Caught and converted to user-friendly message

**HTTPException**:
- Returned to frontend with appropriate status code
- 400: Bad Request (user error)
- 500: Internal Server Error (system error)

## Testing Error Handling

### Test Missing Required Fields

Upload a PDF that's missing period dates to see validation in action.

### Test Preview Mode

1. Upload any statement
2. Clear a required field in preview
3. Try to save - should see validation error

### Test Duplicate Detection

Upload the same PDF twice to see duplicate error message.

## Best Practices

1. **Always Use Preview Mode**: Review data before saving
2. **Check Yellow Fields**: These are missing and may cause errors
3. **Verify Dates**: Ensure dates are in correct format
4. **Read Error Messages**: They tell you exactly what's wrong

## Summary

The improved error handling ensures:
✅ **Clear Messages**: You know exactly which field is missing
✅ **Early Detection**: Errors caught before database insert
✅ **No Partial Data**: All-or-nothing saves
✅ **Automatic Cleanup**: Temp files removed on error
✅ **Fallback Mechanisms**: Smart defaults when possible
✅ **User-Friendly**: Technical errors translated to plain English

## Questions?

If you encounter an error not covered here:
1. Read the full error message - it contains specific details
2. Check if the PDF format is supported
3. Use preview mode to manually verify/edit extracted data
4. Check the parser implementation for your bank
