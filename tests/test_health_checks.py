"""
Regression tests for app.core.health_checks.

These lock in the Phase 4 removal of the database layer. The old
``check_database_connection`` imported SQLAlchemy and asyncpg -- neither of
which was ever declared in any requirements file -- and it ``await``-ed
``get_db()``, an async *generator*, so the healthy path could never execute.
``app/core/database.py`` and the check were deleted; these tests fail loudly
if either is reintroduced.
"""
import importlib
import pathlib

import pytest

from app.core import health_checks
from app.core.config import get_settings


def test_database_module_is_gone():
    """app.core.database must not exist -- it pulled in undeclared deps."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.core.database")


def test_no_database_health_check_symbol():
    """The database health check must not be reintroduced."""
    assert not hasattr(health_checks, "check_database_connection")


@pytest.mark.asyncio
async def test_check_health_reports_no_database_key():
    """check_health must report redis / external_apis / system only."""
    result = await health_checks.check_health(
        include_details=True, settings=get_settings()
    )
    assert set(result["checks"]) == {"redis", "external_apis", "system"}
    assert "database" not in result["checks"]


@pytest.mark.asyncio
async def test_require_healthy_service_rejects_database():
    """'database' is no longer a valid service name."""
    with pytest.raises(ValueError, match="Invalid service: database"):
        await health_checks.require_healthy_service("database")


@pytest.mark.asyncio
async def test_psutil_is_installed_so_system_check_runs():
    """psutil is now a declared dependency; the check must not be 'skipped'."""
    result = await health_checks.check_system_resources()
    assert result["status"] != "skipped", result["message"]
    assert "cpu" in result


def test_slowapi_is_not_a_dependency():
    """slowapi must stay out of requirements.

    It was briefly added because main.py imported it, before that import was
    recognised as a dead path worth deleting rather than feeding: slowapi
    keys by remote address, which is the CRT-8 defect app/core/rate_limiter.py
    exists to fix, and a second limiter would mean two sources of truth for
    keying, storage and the 429 body.
    """
    requirements = (
        pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text()
    declared = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(
        line.lower().startswith("slowapi") for line in declared
    ), "slowapi is declared again; see app/core/rate_limiter.py"


def test_tldextract_is_importable():
    """tldextract is imported unconditionally by the News service."""
    importlib.import_module("tldextract")
