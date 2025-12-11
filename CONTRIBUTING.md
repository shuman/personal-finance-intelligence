# Contributing to Credit Card Statement Analyzer

Thanks for your interest in contributing! This is a hobby project, so contributions of all kinds are welcome—whether you're fixing typos, adding features, or improving documentation.

## 🚀 Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/CreditCardStatementAnalizer.git
   cd CreditCardStatementAnalizer
   ```
3. **Set up development environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## 💡 Ways to Contribute

### 1. Add Support for New Banks

The most valuable contribution! Here's how:

1. **Get sample PDFs** from the bank (remove sensitive data)
2. **Create a new parser** in `app/parsers/`:
   ```python
   from app.parsers.base import BaseParser

   class YourBankParser(BaseParser):
       def can_parse(self, pdf_path: str) -> bool:
           # Detect bank-specific patterns
           pass

       def extract_statement_metadata(self, pdf_path: str) -> dict:
           # Extract statement-level data
           pass

       def extract_transactions(self, pdf_path: str) -> list:
           # Extract transaction list
           pass
   ```
3. **Register in parser factory** (`app/parsers/parser_factory.py`)
4. **Test with multiple statements** to ensure robustness
5. **Document** any bank-specific quirks or requirements

See `app/parsers/amex.py` (672 lines) as a reference implementation.

### 2. Improve Machine Learning

- Experiment with different algorithms (Random Forest, XGBoost, etc.)
- Add more features (transaction time, day of week, recurring patterns)
- Implement ensemble methods
- Add cross-validation and hyperparameter tuning
- Create better evaluation metrics

### 3. Enhance UI/UX

- Add more interactive charts (D3.js, Chart.js)
- Improve mobile responsiveness
- Add dark mode
- Create better data visualizations
- Improve accessibility (ARIA labels, keyboard navigation)

### 4. Add Tests

Currently, there's minimal test coverage. Help by adding:

- Unit tests for parsers
- Integration tests for API endpoints
- Test fixtures with sample data
- Performance benchmarks

### 5. Fix Bugs

Check the [Issues](../../issues) page for known bugs or report new ones.

### 6. Documentation

- Write tutorials or how-to guides
- Add code comments and docstrings
- Create video walkthroughs
- Translate documentation to other languages

## 📝 Code Style Guidelines

### Python Code

- Follow **PEP 8** style guide
- Use **type hints** where possible:
  ```python
  def process_transaction(txn: dict) -> Transaction:
      pass
  ```
- Add **docstrings** for public functions:
  ```python
  def extract_metadata(pdf_path: str) -> dict:
      """
      Extract statement metadata from PDF.

      Args:
          pdf_path: Path to the PDF file

      Returns:
          Dictionary containing statement metadata

      Raises:
          ParsingError: If PDF cannot be parsed
      """
      pass
  ```
- Keep functions **focused** (single responsibility)
- Use **meaningful variable names** (no single letters except loops)

### Frontend Code

- Use **semantic HTML**
- Keep JavaScript **vanilla** (avoid adding heavy frameworks for now)
- Follow **consistent indentation** (2 spaces for HTML/CSS/JS)
- Add **comments** for complex logic

### Git Commits

Write clear, descriptive commit messages:

```bash
# Good
git commit -m "Add Chase bank parser with transaction extraction"

# Bad
git commit -m "fixed stuff"
```

Use conventional commit format when possible:
- `feat:` for new features
- `fix:` for bug fixes
- `docs:` for documentation
- `refactor:` for code refactoring
- `test:` for adding tests

## 🧪 Testing Your Changes

1. **Test manually** with sample PDFs:
   ```bash
   uvicorn app.main:app --reload
   # Upload test statements through UI
   ```

2. **Run existing tests**:
   ```bash
   pytest tests/
   python test_error_handling.py
   ```

3. **Check for errors**:
   ```bash
   python -m py_compile app/**/*.py
   ```

4. **Test ML features**:
   ```bash
   # Train model
   curl -X POST http://localhost:8000/api/ml/train

   # Check stats
   curl http://localhost:8000/api/ml/stats
   ```

## 📤 Submitting Changes

1. **Commit your changes**:
   ```bash
   git add .
   git commit -m "feat: add Chase bank parser"
   ```

2. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

3. **Open a Pull Request** on GitHub:
   - Give it a clear title
   - Describe what changes you made and why
   - Reference any related issues
   - Add screenshots if UI changed

4. **Respond to feedback**:
   - Address review comments promptly
   - Make requested changes in new commits
   - Update documentation if needed

## 🔍 Pull Request Checklist

Before submitting, ensure:

- [ ] Code follows style guidelines
- [ ] No sensitive data (passwords, account numbers) in code
- [ ] New features have basic documentation
- [ ] Existing tests still pass
- [ ] Code compiles without syntax errors
- [ ] Tested with actual PDF statements (if applicable)
- [ ] Updated README.md if adding new features
- [ ] Added entry to CHANGELOG (if exists)

## 🐛 Reporting Bugs

When reporting bugs, please include:

1. **Clear description** of the issue
2. **Steps to reproduce** the bug
3. **Expected behavior** vs actual behavior
4. **Environment details**:
   - Python version
   - Operating system
   - Browser (if UI bug)
5. **Error messages** or logs (remove sensitive data!)
6. **Sample files** (if PDF parsing bug—remove sensitive info!)

## 💬 Questions?

- **General questions**: Open a Discussion
- **Bug reports**: Open an Issue
- **Feature requests**: Open an Issue with "enhancement" label
- **Security issues**: Email privately (don't open public issue)

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

**Thank you for contributing! 🎉**

Every contribution, no matter how small, helps make this project better!
