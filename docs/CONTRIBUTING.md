# Contributing to Headwater API

Thank you for your interest in contributing to the Headwater API! This document provides guidelines and information for contributors.

## Table of Contents

- [Development Setup](#development-setup)
- [Development Workflow](#development-workflow)
- [Code Style](#code-style)
- [Testing](#testing)
- [Commit Message Format](#commit-message-format)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)
- [Documentation](#documentation)
- [Community](#community)

## Development Setup

### Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.9+**: The project requires Python 3.9 or higher
- **Git**: For version control
- **Docker & Docker Compose**: For containerized development
- **Make**: For running development commands (optional but recommended)

### Local Development Setup

1. **Fork the repository**

   ```bash
   git clone https://github.com/yourusername/headwater.git
   cd headwater
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # For development tools
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Run database migrations (if applicable)**
   ```bash
   # If using database migrations
   python -m alembic upgrade head
   ```

6. **Start the development server**
   ```bash
   # Using Docker Compose (recommended)
   docker-compose up -d

   # Or using Python directly
   python main.py
   ```

### Development with Docker

For a fully containerized development environment:

```bash
# Build and start all services
docker-compose up --build

# Run tests in container
docker-compose exec app pytest

# View logs
docker-compose logs -f app
```

### Development Tools Setup

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

This includes:
- `pytest` - Testing framework
- `black` - Code formatting
- `flake8` - Linting
- `mypy` - Type checking
- `pre-commit` - Git hooks

## Development Workflow

### 1. Choose an Issue

- Check the [GitHub Issues](https://github.com/rainmanjam/headwater/issues) for open tasks
- Look for issues labeled `good first issue` or `help wanted`
- Comment on the issue to indicate you're working on it

### 2. Create a Feature Branch

```bash
# Create and switch to a new branch
git checkout -b feature/your-feature-name

# Or for bug fixes
git checkout -b fix/issue-number-description
```

### 3. Make Your Changes

- Write clean, well-documented code
- Follow the established code style
- Add tests for new functionality
- Update documentation as needed

### 4. Test Your Changes

```bash
# Run the full test suite
pytest

# Run specific tests
pytest tests/test_specific_feature.py

# Run with coverage
pytest --cov=app --cov-report=html

# Run linting
flake8 app/
black --check app/
mypy app/
```

### 5. Commit Your Changes

```bash
# Stage your changes
git add .

# Commit with a descriptive message
git commit -m "feat: add new autocomplete endpoint"
```

### 6. Push and Create Pull Request

```bash
# Push your branch
git push origin feature/your-feature-name

# Create a Pull Request on GitHub
```

## Code Style

### Python Style Guidelines

This project follows PEP 8 with some modifications:

- **Line Length**: 88 characters (Black default)
- **Imports**: Use absolute imports
- **Type Hints**: Required for all public functions
- **Docstrings**: Google-style docstrings for all public functions

### Code Formatting

We use [Black](https://black.readthedocs.io/) for automatic code formatting:

```bash
# Format code
black app/

# Check formatting without changes
black --check app/
```

### Linting

We use [Flake8](https://flake8.pycqa.org/) for linting:

```bash
# Run linting
flake8 app/
```

### Type Checking

We use [MyPy](https://mypy.readthedocs.io/) for static type checking:

```bash
# Run type checking
mypy app/
```

### Pre-commit Hooks

Install pre-commit hooks to automatically run checks before commits:

```bash
pip install pre-commit
pre-commit install

# Run manually
pre-commit run --all-files
```

## Testing

### Test Structure

```
tests/
├── unit/           # Unit tests
├── integration/    # Integration tests
├── e2e/           # End-to-end tests
└── fixtures/      # Test data and fixtures
```

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_google_news_api.py

# Run tests matching pattern
pytest -k "test_autocomplete"

# Run with coverage
pytest --cov=app --cov-report=html
```

### Writing Tests

```python
import pytest
from app.api.google_news import GoogleNewsAPI

class TestGoogleNewsAPI:
    def test_search_basic(self):
        """Test basic news search functionality."""
        api = GoogleNewsAPI()
        results = api.search("artificial intelligence")
        
        assert len(results) > 0
        assert "title" in results[0]
        assert "link" in results[0]

    def test_search_with_filters(self):
        """Test news search with country and language filters."""
        api = GoogleNewsAPI()
        results = api.search(
            query="climate change",
            country="US",
            language="en",
            max_results=5
        )
        
        assert len(results) <= 5
        # Add more assertions...
```

### Test Coverage

Maintain test coverage above 80%:

```bash
# Generate coverage report
pytest --cov=app --cov-report=html

# Open coverage report in browser
open htmlcov/index.html
```

## Commit Message Format

We follow the [Conventional Commits](https://conventionalcommits.org/) specification:

```
type(scope): description

[optional body]

[optional footer]
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

### Scopes

- `api` - API-related changes
- `core` - Core functionality
- `config` - Configuration changes
- `docs` - Documentation
- `tests` - Test-related changes

### Examples

```
feat(api): add new google trends endpoint

fix(core): resolve memory leak in cache manager

docs: update API reference for v1.1.0

test(api): add integration tests for autocomplete
```

## Pull Request Process

### Before Submitting

1. **Update your branch** with the latest changes from main:
   ```bash
   git checkout main
   git pull origin main
   git checkout your-branch
   git rebase main
   ```

2. **Run all checks**:
   ```bash
   # Run tests
   pytest
   
   # Run linting and formatting
   flake8 app/
   black --check app/
   mypy app/
   
   # Run pre-commit hooks
   pre-commit run --all-files
   ```

3. **Update documentation** if needed

### Pull Request Template

When creating a PR, please fill out the template with:

- **Description**: What changes were made and why
- **Type of Change**: Bug fix, feature, documentation, etc.
- **Testing**: How the changes were tested
- **Breaking Changes**: Any breaking changes
- **Screenshots**: UI changes (if applicable)

### Review Process

1. **Automated Checks**: CI/CD pipeline runs tests and linting
2. **Code Review**: At least one maintainer reviews the code
3. **Approval**: PR is approved and merged
4. **Deployment**: Changes are automatically deployed

## Reporting Issues

### Bug Reports

When reporting bugs, please include:

1. **Clear title** describing the issue
2. **Steps to reproduce** the problem
3. **Expected behavior** vs actual behavior
4. **Environment details**:
   - OS and version
   - Python version
   - Dependencies versions
5. **Error messages** and stack traces
6. **Screenshots** if applicable

### Feature Requests

For new features, please include:

1. **Use case**: What problem does this solve?
2. **Proposed solution**: How should it work?
3. **Alternatives**: Other approaches considered
4. **Additional context**: Any other relevant information

## Documentation

### Updating Documentation

When making changes that affect users:

1. **Update README.md** if needed
2. **Update API documentation** for endpoint changes
3. **Add code examples** for new features
4. **Update CHANGELOG.md** with changes

### Documentation Standards

- Use Markdown for all documentation
- Include code examples where helpful
- Keep language clear and concise
- Test all code examples

## Community

### Getting Help

- **GitHub Issues**: For bugs and feature requests
- **GitHub Discussions**: For general questions and discussions
- **Discord**: For real-time chat and community support

### Code of Conduct

Please review and follow our [Code of Conduct](CODE_OF_CONDUCT.md).

### Recognition

Contributors are recognized in:
- CHANGELOG.md for significant contributions
- GitHub's contributor insights
- Social media mentions (with permission)

## Additional Resources

- [API Reference](API_REFERENCE.md)
- [Architecture Overview](ARCHITECTURE_OVERVIEW.md)
- [Deployment Guide](DEPLOYMENT.md)
- [Troubleshooting Guide](TROUBLESHOOTING.md)
- [Security Guidelines](SECURITY_GUIDELINES.md)

---

Thank you for contributing to Headwater API! Your contributions help make this project better for everyone. 🚀
