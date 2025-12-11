# Credit Card Statement Analyzer 💳

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code style: PEP 8](https://img.shields.io/badge/code%20style-PEP%208-orange.svg)](https://www.python.org/dev/peps/pep-0008/)

> A fun Python project exploring machine learning-powered transaction analysis, expense pattern detection, and financial forecasting.

This is a hobby project built to experiment with Python, PDF parsing, and ML techniques. It automatically extracts transaction data from credit card statements, learns spending patterns using machine learning, and provides analytics to forecast upcoming expenses. Since this is a side project, it's actively evolving—expect some rough edges and incomplete features!

**Feel free to contribute if you enjoy working with Python. Happy coding! 🚀**

---

## 🎬 Quick Demo

```bash
# Clone and setup
git clone https://github.com/yourusername/CreditCardStatementAnalizer.git
cd CreditCardStatementAnalizer
chmod +x setup.sh && ./setup.sh

# Start the server
source .venv/bin/activate
uvicorn app.main:app --reload

# Visit http://localhost:8000
# Upload a statement, categorize transactions, train ML model!
```

## 📸 Screenshots

<details>
<summary>Click to view screenshots (Coming Soon)</summary>

- **Upload Interface**: Drag-and-drop PDF upload with password protection
- **Transaction List**: Sortable, filterable transactions with category labels
- **Global Search**: Search across all statements with advanced filters
- **Analytics Dashboard**: Visual spending trends and category breakdowns
- **ML Prediction**: Automatic category suggestions with confidence scores

*Screenshots will be added in future releases*

</details>

---

## 📑 Table of Contents

- [Quick Demo](#-quick-demo)
- [Screenshots](#-screenshots)
- [Features](#-features)
- [Technology Stack](#️-technology-stack)
- [Quick Start](#-quick-start)
- [Usage Guide](#-usage-guide)
- [Architecture](#️-architecture-overview)
- [Adding New Banks](#-adding-support-for-new-banks)
- [Error Handling](#-error-handling--troubleshooting)
- [Development](#-development)
- [Known Issues](#-known-issues--limitations)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### Core Functionality
- **📄 PDF Upload & Parsing**: Supports password-protected credit card statement PDFs
- **🤖 Machine Learning Categorization**: Automatically learns transaction patterns from your manual corrections using TF-IDF + Naive Bayes
- **📊 Expense Analytics**: Visual spending trends, category breakdowns, and merchant analysis
- **🔍 Transaction Search**: Find any transaction across all statements by date, description, amount, or category
- **📈 Forecasting**: Analyze historical data to predict upcoming month expenses (experimental)
- **💾 Data Export**: Export transactions and analytics to CSV for external analysis
- **🏦 Multi-Bank Support**: Extensible parser architecture (currently supports American Express)

### Machine Learning Features
- **Pattern Recognition**: TF-IDF vectorization with Multinomial Naive Bayes classifier
- **Incremental Learning**: Model improves as you manually categorize transactions
- **Confidence Scoring**: Shows prediction confidence for each categorization
- **Training API**: RESTful endpoints to train, predict, and get model statistics

## 🛠️ Technology Stack

- **Backend**: FastAPI 0.109.0 (Async Python 3.13+)
- **Database**: SQLite with async SQLAlchemy 2.0
- **PDF Processing**: pdfplumber, pikepdf, PyMuPDF
- **Machine Learning**: scikit-learn 1.4.0, numpy 1.26.3
- **Frontend**: Jinja2 Templates + Tailwind CSS + JavaScript
- **Data Validation**: Pydantic v2

## 🚀 Quick Start

### Prerequisites
- Python 3.13+ (or Python 3.10+)
- pip and venv

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/shuman/CreditCardStatementAnalizer.git
cd CreditCardStatementAnalizer
```

2. **Create virtual environment**
```bash
python3 -m venv .venv
source .venv/bin/activate  # On macOS/Linux
# or
.venv\Scripts\activate  # On Windows
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Run the application**
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

5. **Access the application**

   Open your browser to: `http://localhost:8000`

### First Time Setup

After uploading your first few statements:

1. **Manually categorize** some transactions (click Edit on transaction rows)
2. **Train the ML model** to learn patterns:
   ```bash
   curl -X POST http://localhost:8000/api/ml/train
   ```
3. **Let the model predict** categories for new uploads automatically!

Check model performance:
```bash
curl http://localhost:8000/api/ml/stats
```

## 📖 Usage Guide

### 1. Upload Your First Statement

1. Navigate to `http://localhost:8000`
2. **Drag and drop** or click to select your PDF statement
3. Enter the **PDF password** (if protected)
4. Select your **bank** (American Express currently supported)
5. Click **"Upload & Process"**

The parser will extract:
- Statement metadata (dates, balances, credit limits)
- All transactions (35+ fields per transaction)
- Fees and interest charges
- Rewards/points summary
- Category-wise spending breakdown

### 2. Review & Edit Transactions

- Click on any statement to view **detailed transactions**
- **Edit categories** manually by clicking on transactions
- **Filter** by date, category, merchant, or amount
- **Sort** by any column
- **Export to CSV** for spreadsheet analysis

### 3. Train the ML Model

After categorizing 20-30 transactions manually:

```bash
# Train the model
curl -X POST http://localhost:8000/api/ml/train

# Check training stats
curl http://localhost:8000/api/ml/stats
```

The model learns patterns like:
- "SWIGGY" → Food & Dining
- "AMAZON" → Shopping
- "SHELL" → Fuel
- "NETFLIX" → Entertainment

### 4. Global Transaction Search

Visit `/transactions` to search across **all statements**:
- Search by description (partial match)
- Filter by date range
- Filter by amount or category
- Export filtered results

### 5. View Analytics

Navigate to the **Analytics** page to:
- See spending trends over time
- Analyze category-wise breakdown
- Identify top merchants
- Track credit utilization
- Monitor rewards earning patterns

## Project Structure

```
CreditCardStatementAnalizer/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application entry point
│   ├── config.py               # Configuration settings
│   ├── database.py             # Database connection setup
│   ├── models/                 # SQLAlchemy database models
│   │   ├── statement.py
│   │   ├── transaction.py
│   │   └── ...
│   ├── parsers/                # Bank-specific PDF parsers
│   │   ├── base.py             # Abstract base parser
│   │   ├── amex.py             # Amex parser implementation
│   │   └── parser_factory.py
│   ├── services/               # Business logic layer
│   │   ├── pdf_service.py
│   │   └── statement_service.py
│   ├── routers/                # API endpoints
│   │   ├── upload.py
│   │   ├── statements.py
│   │   └── analytics.py
│   └── utils/                  # Helper utilities
│       └── categorization.py
├── templates/                  # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── statement_list.html
│   └── statement_detail.html
├── static/                     # Static assets
│   ├── css/
│   ├── js/
│   └── uploads/                # Uploaded PDF files
├── tests/                      # Unit tests
├── alembic/                    # Database migrations
├── requirements.txt
└── README.md
```

## Database Schema

The application uses a comprehensive schema to store maximum data for analysis:

- **statements**: Statement-level metadata (30+ fields)
- **transactions**: Individual transactions (35+ fields)
- **fees**: Fee breakdown with GST
- **interest_charges**: Interest calculation details
- **rewards_summary**: Rewards/points tracking
- **category_summary**: Category-wise spending
- **payments**: Payment history

## 🏗️ Architecture Overview

```
┌─────────────────┐
│   Upload PDF    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────────┐
│  PDF Parser     │────▶│  ML Categorizer  │
│  (Bank-Specific)│     │  (TF-IDF + NB)   │
└────────┬────────┘     └──────────────────┘
         │
         ▼
┌─────────────────┐
│  SQLite DB      │
│  - Statements   │
│  - Transactions │
│  - Analytics    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  FastAPI Routes │
│  - Web UI       │
│  - REST API     │
└─────────────────┘
```

### Key Components

1. **PDF Parsers** (`app/parsers/`)
   - Bank-specific extraction logic
   - Handles password-protected PDFs
   - Extensible for new banks

2. **ML Categorizer** (`app/ml/categorizer.py`)
   - TF-IDF feature extraction
   - Naive Bayes classification
   - Incremental learning support

3. **Services Layer** (`app/services/`)
   - Business logic separation
   - Transaction management
   - Error handling with clear messages

4. **RESTful API** (`app/routers/`)
   - Upload endpoints
   - Transaction search
   - ML training/prediction
   - Analytics data

## 🔧 Adding Support for New Banks

Want to add Chase, Citibank, or your local bank? Here's how:

1. **Create a new parser** in `app/parsers/` (e.g., `chase.py`)

```python
from app.parsers.base import BaseParser

class ChaseParser(BaseParser):
    def can_parse(self, pdf_path: str) -> bool:
        """Detect if this is a Chase statement"""
        # Check for Chase-specific text/layout
        pass

    def extract_statement_metadata(self, pdf_path: str) -> dict:
        """Extract statement-level data"""
        pass

    def extract_transactions(self, pdf_path: str) -> list:
        """Extract transaction list"""
        pass
```

2. **Register in** `app/parsers/parser_factory.py`:
```python
from app.parsers.chase import ChaseParser

parsers = [AmexParser(), ChaseParser()]
```

3. **Test with sample PDFs** and iterate!

See `app/parsers/amex.py` for a complete reference implementation (672 lines).

## 🐛 Error Handling & Troubleshooting

The application provides comprehensive error handling:

- **Missing Required Fields**: Clear messages showing exactly which fields are missing
- **Duplicate Detection**: Database-level constraints prevent duplicate transactions
- **Validation Before Save**: All fields validated before database insert
- **Automatic Fallbacks**: Smart defaults (e.g., 30-day billing cycle for missing dates)
- **Clear API Errors**: Structured JSON error responses with actionable messages

### Common Issues

| Issue | Solution |
|-------|----------|
| "Missing required fields" | Check if PDF has statement period dates. Parser will auto-calculate if missing. |
| "UNIQUE constraint failed" | Duplicate transaction detected (same date, amount, description). Already exists in database. |
| PDF won't parse | Verify password is correct. Check if PDF is text-based (not scanned image). |
| ML model not predicting | Train model first with `curl -X POST http://localhost:8000/api/ml/train` |

See [ERROR_HANDLING_GUIDE.md](ERROR_HANDLING_GUIDE.md) for detailed troubleshooting.

## 🧪 Development

### Running in Development Mode

```bash
# With auto-reload (recommended)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Or using the shell script
chmod +x run.sh
./run.sh
```

### Project Structure
```
CreditCardStatementAnalizer/
├── app/
│   ├── main.py                    # FastAPI entry point
│   ├── config.py                  # Settings & configuration
│   ├── database.py                # Async SQLAlchemy setup
│   ├── models/                    # Database models (ORM)
│   ├── parsers/                   # Bank-specific PDF parsers
│   │   ├── base.py               # Abstract base class
│   │   ├── amex.py               # American Express (672 lines)
│   │   └── parser_factory.py    # Parser selection logic
│   ├── ml/                        # Machine learning
│   │   └── categorizer.py        # TF-IDF + Naive Bayes (278 lines)
│   ├── services/                  # Business logic layer
│   │   └── statement_service.py  # Core processing (596 lines)
│   ├── routers/                   # API endpoints
│   │   ├── upload.py             # File upload
│   │   ├── statements.py         # CRUD + search
│   │   └── ml.py                 # ML endpoints
│   └── utils/                     # Helpers
├── templates/                     # Jinja2 HTML templates
│   ├── base.html                 # Base layout
│   ├── index.html                # Upload page
│   ├── statement_list.html       # Statement listing
│   ├── statement_detail.html     # Transaction details
│   └── all_transactions.html     # Global search
├── static/                        # Static assets
│   ├── uploads/                  # PDF storage
│   └── models/                   # Trained ML models
├── requirements.txt               # Python dependencies
└── statements.db                  # SQLite database
```

### Database Schema

Comprehensive schema storing 100+ fields per statement:

- **statements** (30+ fields): Metadata, balances, dates, limits
- **transactions** (35+ fields): Date, amount, merchant, category, location, etc.
- **fees**: Fee breakdown with GST
- **interest_charges**: Interest calculation details
- **rewards_summary**: Points/cashback tracking
- **category_summary**: Category-wise spending aggregation
- **payments**: Payment history

### Running Tests

```bash
# Run all tests
pytest tests/

# Test error handling
python test_error_handling.py

# Test duplicate detection
python test_duplicate_handling.py
```

## 🚧 Known Issues & Limitations

This is a hobby project, so there are some rough edges:

- ⚠️ **Only American Express supported** currently (other banks need parser implementation)
- ⚠️ **No user authentication** (single-user deployment only)
- ⚠️ **Basic forecasting** (experimental, needs more sophisticated models)
- ⚠️ **SQLite only** (no PostgreSQL/MySQL support yet)
- ⚠️ **No automated tests** for parsers (manual testing with sample PDFs)
- ⚠️ **Password stored in plaintext** (not production-ready)

## 🎯 Future Ideas

Since this is a fun side project, here are potential enhancements:

- [ ] **Advanced ML**: LSTM/Transformer models for better pattern recognition
- [ ] **Anomaly Detection**: Flag unusual spending patterns
- [ ] **Budget Tracking**: Set category budgets with alerts
- [ ] **Expense Forecasting**: Predict next month's spending with confidence intervals
- [ ] **Multi-user Support**: Add authentication and user isolation
- [ ] **More Banks**: Chase, Citibank, HSBC, local banks
- [ ] **Mobile App**: React Native or Flutter frontend
- [ ] **Expense Sharing**: Split bills, track shared expenses
- [ ] **Receipt OCR**: Extract data from receipt photos
- [ ] **Smart Recommendations**: Suggest better cards based on spending

Pull requests welcome for any of these! 🙌

## 🔒 Security Considerations

**⚠️ Important: This is NOT production-ready for handling real financial data!**

Current limitations:
- PDF passwords stored in plaintext
- No encryption at rest for sensitive data
- No user authentication/authorization
- No API rate limiting
- No input sanitization for SQL injection (though SQLAlchemy helps)

**For personal/learning use only.** Do NOT deploy publicly without:
1. Adding proper authentication (OAuth2, JWT)
2. Encrypting sensitive data at rest
3. Using environment variables for secrets
4. Implementing rate limiting and CORS
5. Adding comprehensive input validation
6. Security audit and penetration testing

## 📄 License

MIT License - Feel free to use, modify, and distribute!

## 🤝 Contributing

This is a hobby project, but contributions are absolutely welcome!

### How to Contribute

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/amazing-feature`
3. **Make your changes** and test thoroughly
4. **Commit with clear messages**: `git commit -m 'Add amazing feature'`
5. **Push to your fork**: `git push origin feature/amazing-feature`
6. **Open a Pull Request** with description of changes

### Contribution Ideas

- Add parsers for new banks (Chase, Citibank, etc.)
- Improve ML accuracy with better features/models
- Add unit tests (especially for parsers)
- Enhance UI/UX with better visualizations
- Fix bugs or improve error handling
- Write documentation or tutorials
- Add support for other statement types (utility bills, etc.)

### Code Style

- Follow PEP 8 for Python code
- Use type hints where possible
- Add docstrings for public functions
- Keep functions focused and testable

## 💬 Questions or Issues?

- **Found a bug?** Open an issue with details and sample data (remove sensitive info!)
- **Have a question?** Start a discussion or open an issue
- **Want to chat?** Feel free to reach out!

---

**Built with ❤️ and Python by someone who enjoys parsing PDFs and training models!**

*Happy coding! 🚀*
