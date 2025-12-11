# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial public release
- Complete README with comprehensive documentation
- API documentation (API_DOCUMENTATION.md)
- Contributing guidelines (CONTRIBUTING.md)
- MIT License
- Machine learning categorization with TF-IDF + Naive Bayes
- Global transaction search across all statements
- Transaction export to CSV
- Error handling guide with troubleshooting
- Automatic date fallback (30-day billing cycle)

### Features
- PDF parsing for American Express statements
- Password-protected PDF support
- Transaction categorization (manual + ML)
- Spending analytics and visualizations
- Rewards/points tracking
- Fee and interest tracking
- RESTful API with FastAPI
- Async SQLAlchemy with SQLite
- Machine learning endpoints (train, predict, stats)
- Incremental learning from user corrections
- Comprehensive database schema (100+ fields per statement)

### Technical
- FastAPI 0.109.0 backend
- SQLAlchemy 2.0.25 async ORM
- scikit-learn 1.4.0 for ML
- pdfplumber + pikepdf for PDF processing
- Jinja2 templates with Tailwind CSS
- Python 3.13+ support

## [0.1.0] - 2025-12-11

### Initial Development
- Basic PDF parsing implementation
- Database schema design
- American Express parser (672 lines)
- Statement service with transaction management
- Upload interface with drag-and-drop
- Statement listing and detail views
- Category summarization
- Basic error handling

---

## Version History

This project started as a learning experiment and evolved into a full-featured application. Early versions had:
- Complex duplicate detection logic (removed for simplicity)
- Partial transaction saves (simplified to atomic transactions)
- Multiple rollback attempts (now single commit pattern)

Current version focuses on:
- Simplicity over complexity
- Clear error messages
- Atomic database transactions
- Extensible architecture for new banks
- Machine learning integration

---

## Future Roadmap

See README.md for planned enhancements and contribution ideas.
