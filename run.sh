#!/bin/bash
# Run script for Credit Card Statement Analyzer

echo "Starting Credit Card Statement Analyzer..."
echo ""

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "✓ Virtual environment activated"
else
    echo "⚠ Virtual environment not found. Creating..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
fi

echo ""
echo "Starting FastAPI server..."
echo "Access the application at: http://localhost:8000"
echo ""

# Run the application
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
