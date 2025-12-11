#!/bin/bash

# Credit Card Statement Analyzer - Setup Script
# Automated setup for development environment

set -e  # Exit on error

echo "🚀 Credit Card Statement Analyzer - Setup"
echo "=========================================="
echo ""

# Check Python version
echo "✓ Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
required_version="3.10"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Python 3.10+ required. Found: $python_version"
    exit 1
fi
echo "   Found Python $python_version ✓"
echo ""

# Create virtual environment
echo "📦 Creating virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "   Created .venv ✓"
else
    echo "   .venv already exists ✓"
fi
echo ""

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source .venv/bin/activate
echo "   Activated ✓"
echo ""

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip > /dev/null 2>&1
echo "   Upgraded pip ✓"
echo ""

# Install dependencies
echo "📥 Installing dependencies..."
echo "   This may take a few minutes..."
pip install -r requirements.txt > /dev/null 2>&1
echo "   Installed all dependencies ✓"
echo ""

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p static/uploads
mkdir -p static/models
mkdir -p templates
echo "   Directories created ✓"
echo ""

# Check if database exists
if [ ! -f "statements.db" ]; then
    echo "💾 Database not found - will be created on first run"
else
    echo "💾 Database found: statements.db ✓"
fi
echo ""

# Create .env file if doesn't exist
if [ ! -f ".env" ]; then
    echo "⚙️  Creating .env file..."
    cat > .env << EOF
# Application Settings
APP_NAME="Credit Card Statement Analyzer"
DEBUG=true
HOST=127.0.0.1
PORT=8000

# Database
DATABASE_URL=sqlite+aiosqlite:///./statements.db

# Upload Directory
UPLOAD_DIR=./static/uploads

# ML Model Directory
MODEL_DIR=./static/models
EOF
    echo "   Created .env ✓"
else
    echo "⚙️  .env already exists ✓"
fi
echo ""

echo "✅ Setup completed successfully!"
echo ""
echo "📝 Next steps:"
echo ""
echo "1. Activate the virtual environment:"
echo "   source .venv/bin/activate"
echo ""
echo "2. Start the application:"
echo "   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
echo ""
echo "3. Open your browser:"
echo "   http://localhost:8000"
echo ""
echo "4. Upload your first statement and categorize some transactions"
echo ""
echo "5. Train the ML model:"
echo "   curl -X POST http://localhost:8000/api/ml/train"
echo ""
echo "📚 Documentation:"
echo "   - README.md - Full documentation"
echo "   - API_DOCUMENTATION.md - API reference"
echo "   - CONTRIBUTING.md - Contribution guide"
echo "   - ERROR_HANDLING_GUIDE.md - Troubleshooting"
echo ""
echo "🎉 Happy coding!"
