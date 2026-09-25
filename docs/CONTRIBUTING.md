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
- [Additional Resources](#additional-resources)

## Development Setup

### Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.14**: The Docker image (`python:3.14-slim-trixie`), CI and `requirements.lock` all target 3.14, and ruff is configured with `target-version = "py314"`
- **Git**: For version control
- **Docker & Docker Compose**: For containerized development
- **Make**: For running development commands (optional but recommended)

### Local Development Setup

1. **Fork the repository** (https://github.com/HouseofLoops/headwater) and clone your fork

   ```bash
   git clone https://github.com/<your-username>/headwater.git
   cd headwater
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements-dev.txt  # includes requirements.txt (same as `make install`)
   playwright install chromium          # only needed to run the Maps scraper locally
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Start the development server**
   ```bash
   # With auto-reload on port 8000 (same as `make run` / `make dev`)
   uvicorn main:app --reload --host 0.0.0.0 --port 8000

   # Or the full stack (API + Redis) in Docker
   docker compose up --build -d
   docker compose logs -f web
   ```

   Redis is optional locally: without `REDIS_URL` the rate limiter, cache and
   Maps records use process memory. The production image does not contain the
   test dependencies, so run `pytest` on the host.

### Development Tools

`requirements-dev.txt` includes:
- `pytest`, `pytest-cov`, `pytest-asyncio` - Testing and coverage
- `ruff` - Linting and formatting (the only linter/formatter; configured in `pyproject.toml`)
- `mypy` - Type checking
- `pip-audit` - Dependency vulnerability audit

`pre-commit` is not pinned in `requirements-dev.txt`; install it separately (see [Pre-commit Hooks](#pre-commit-hooks)).

## Development Workflow

### 1. Choose an Issue

- Check the [GitHub Issues](https://github.com/HouseofLoops/headwater/issues) for open tasks
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

# Run one test file (replace <module> with a real name, e.g. tests/test_proxy.py)
pytest tests/test_<module>.py

# Run with coverage
pytest --cov=app --cov-report=html

# Run linting and the format check (the same gates CI enforces)
ruff check .
ruff format --check .
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

- **Line Length**: 120 characters (`line-length` in `[tool.ruff]`)
- **Imports**: Use absolute imports; ordering is checked by ruff's `I` (isort) rules
- **Type Hints**: Required for all public functions
- **Docstrings**: Google-style docstrings for all public functions

[Ruff](https://docs.astral.sh/ruff/) is the only linter and formatter. Its configuration lives in
`pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.format]`); there is no `.flake8`,
`setup.cfg` or separate isort/black config. Install the version pinned in `requirements-dev.txt`, which
is kept in step with CI and pre-commit.

### Linting

CI (`.github/workflows/_verify.yml`, job "Lint (ruff)") runs the full rule set from
`pyproject.toml` and fails on any finding; the pre-commit ruff hook runs the same rules.

```bash
ruff check .
```

`make lint` runs the same check plus the format check. The repository is at zero findings, so a
new one is yours to fix. If a finding is a deliberate exception, silence that line with
`# noqa: <RULE> - <reason>` rather than adding the rule to `lint.ignore`.

```bash
ruff check path/to/changed_file.py
ruff check --fix path/to/changed_file.py
```

### Code Formatting

The whole repository is formatted with `ruff format`, and CI (and the `ruff-format` pre-commit
hook) fails on unformatted files:

```bash
# Format the files you changed
ruff format path/to/changed_file.py

# Check without writing
ruff format --check path/to/changed_file.py
```

### Type Checking

We use [MyPy](https://mypy.readthedocs.io/) for static type checking:

```bash
# Run type checking
mypy app/
```

mypy is installed by `requirements-dev.txt` but is not run in CI, so treat its output as advisory.

### Pre-commit Hooks

`.pre-commit-config.yaml` runs the standard file hygiene hooks (trailing whitespace, end-of-file,
YAML/TOML/JSON validity, merge conflicts, private keys), ruff with the full rule set from
`pyproject.toml` (`--no-fix`) and `ruff-format --check`, bandit, hadolint (needs Docker) and shellcheck.

```bash
pip install pre-commit
pre-commit install

# Run on the files you changed
pre-commit run --files path/to/changed_file.py

# Run everything (the first run is noisy: some hooks report pre-existing findings)
pre-commit run --all-files
```

## Testing

### Test Structure

```
tests/
├── conftest.py     # Shared fixtures; pins the settings source so .env does not leak in
├── test_*.py       # Most tests live at the top level, one file per module
├── unit/           # Unit tests
└── integration/    # Integration tests
```

`tests/conftest.py` blocks outbound network access in every test: mock the HTTP
client, or mark a test `@pytest.mark.allow_network` if it genuinely must reach the
network. Markers are declared in `pyproject.toml` (`--strict-markers` is on).

CI runs `pytest --cov=app` and fails below 65% coverage (the `coverage-floor` input in `_verify.yml`).

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

Use the fixtures in `tests/conftest.py`: `test_client` (a `TestClient` on the real
`main:app`, with startup and shutdown run), `settings`, `override_settings`,
`api_headers` and `fake_redis`. A minimal example:

```python
def test_health_is_public(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_status_rejects_a_missing_key(test_client):
    assert test_client.get("/status").status_code == 401
```

The test environment configures no API keys; `tests/test_auth.py` shows how to
set `API_KEYS` for a test. Endpoint tests that would reach Google must mock the
service layer; see `tests/test_google_news_api.py` and
`tests/test_google_trends_api.py` for the pattern.

### Test Coverage

CI enforces the 65% floor above; new code should not lower it.

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

1. **Update your branch** with the latest `main` from the upstream repository:
   ```bash
   git remote add upstream https://github.com/HouseofLoops/headwater.git  # once
   git fetch upstream
   git rebase upstream/main
   ```

2. **Run the same checks as CI**:
   ```bash
   pytest --cov=app
   make lint          # ruff check . && ruff format --check .
   pre-commit run --files path/to/changed_file.py
   ```

3. **Update documentation** if behaviour, configuration or endpoints change.

### What to put in the PR description

There is no PR template. Describe what changed and why, how you tested it, and
any breaking change (renamed settings, changed response shapes, removed endpoints).

### Review and release

1. CI (`.github/workflows/main.yml`, which calls `_verify.yml`) runs ruff, the
   tests with the coverage floor, `pip-audit`, a boot from `.env.example`, and a
   Docker build with a health check.
2. A maintainer reviews; `main` only accepts changes through pull requests with
   passing CI.
3. Releases are cut by bumping `app/__version__.py` in a PR (`make version-patch`,
   `make version-minor` or `make version-major`). On merge, `release.yml` tags the
   version and publishes signed multi-arch images to Docker Hub and GHCR.

## Reporting Issues

Open issues at https://github.com/HouseofLoops/headwater/issues.

- **Bugs**: steps to reproduce, expected vs actual behaviour, Headwater version
  (`GET /status` or the image tag), how you run it (Docker, bare uvicorn),
  relevant settings with secrets removed, and the error body or log lines.
- **Feature requests**: the use case, the proposed behaviour and alternatives
  you considered.
- **Security issues**: do not file a public issue; see
  [SECURITY_GUIDELINES.md](SECURITY_GUIDELINES.md#reporting-a-vulnerability).

## Documentation

When a change affects users:

1. Update the relevant file in `docs/` (and `README.md` if needed).
2. Update [API_REFERENCE.md](API_REFERENCE.md) for endpoint changes and
   `.env.example` for new settings.
3. Add an entry to [CHANGELOG.md](CHANGELOG.md).

Every endpoint, setting, path and command in the docs must exist in the code.
Run the examples you add.

## Additional Resources

- [API Reference](API_REFERENCE.md)
- [Architecture Overview](ARCHITECTURE_OVERVIEW.md)
- [Deployment Guide](DEPLOYMENT.md)
- [Troubleshooting Guide](TROUBLESHOOTING.md)
- [Security Guidelines](SECURITY_GUIDELINES.md)
