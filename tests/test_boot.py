"""
Boot tests: the app must start from its own documented configuration.

These are the tests whose absence let CRT-1 (".env.example forbids boot") and
CRT-4 ("disabling auth inverts it") ship.

Every test here runs the real ``.env.example`` verbatim through
pydantic-settings, so a variable added to the example file that ``Settings``
rejects fails the suite immediately.

No network access: the one protected route exercised here has its upstream
fetch patched out.
"""

import importlib
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"


# The API key shipped in .env.example. Read from the file rather than
# hard-coded so the two cannot drift apart.
def _example_env() -> dict:
    """Parse .env.example into a plain dict of KEY -> value."""
    values = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _example_documented_keys() -> set:
    """Every Settings key .env.example mentions, including commented-out ones.

    A key deliberately left unset -- REDIS_URL, because the example file must
    stay runnable without Docker -- is still documented if it appears as a
    commented assignment. Drift detection cares that the key is described;
    whether it ships with a value is a separate decision.
    """
    keys = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip().lstrip("#").strip()
        if "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if key.isidentifier() and key.isupper():
            keys.add(key)
    return keys


EXAMPLE_ENV = _example_env()
EXAMPLE_DOCUMENTED_KEYS = _example_documented_keys()
EXAMPLE_API_KEY = EXAMPLE_ENV["API_KEYS"]


@pytest.fixture
def env_example_dir(tmp_path):
    """Copy .env.example verbatim to a temp dir as .env and yield the dir."""
    target = tmp_path / ".env"
    shutil.copyfile(ENV_EXAMPLE, target)
    return tmp_path


@pytest.fixture
def app_with_env(monkeypatch, env_example_dir):
    """
    Build the application with .env.example as its configuration.

    Settings are pointed at the copied file, the settings cache is cleared,
    and app.core.auth is reloaded so its key set is rebuilt.
    """

    def _build(**overrides):
        for key, value in overrides.items():
            monkeypatch.setenv(key, value)

        from app.core.config import Settings, get_settings

        env_path = str(env_example_dir / ".env")
        monkeypatch.setitem(Settings.model_config, "env_file", env_path)
        get_settings.cache_clear()

        from app.core import auth as auth_module

        auth_module.initialize_api_keys(get_settings())

        import main

        importlib.reload(main)
        return main.create_application()

    yield _build

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.core import auth as auth_module

    auth_module.initialize_api_keys(get_settings())


def _protected_client(app) -> TestClient:
    """
    Attach a protected probe route that uses the real auth dependency.

    Using a dedicated route keeps the assertion about authentication only --
    no upstream scraper is reachable, so a 200 cannot come from the network
    and a 500 cannot be blamed on one.
    """
    from app.core.auth import get_api_key

    @app.get("/__boot_probe__")
    async def _probe(api_key: str = Depends(get_api_key)):
        return {"ok": True, "api_key": api_key}

    return TestClient(app)


class TestBootFromDocumentedConfig:
    """CRT-1: the app must import and boot from .env.example verbatim."""

    def test_settings_load_from_env_example(self, env_example_dir):
        """
        Loading .env.example must not raise.

        Before the fix this raised ValidationError (extra_forbidden) for
        every key the file ships that is not a Settings field. The POSTGRES_*
        keys are gone with the database layer, but DATAFORSEO_LOGIN,
        DATAFORSEO_PASSWORD and REDIS_PASSWORD remain, so extra="ignore" is
        still genuinely exercised here.
        """
        from app.core.config import Settings

        settings = Settings(_env_file=str(env_example_dir / ".env"))
        assert settings.API_KEYS == [EXAMPLE_API_KEY]

    def test_import_main_in_clean_process_with_env_example(self, env_example_dir):
        """
        `import main` must succeed in a fresh process whose cwd holds the
        .env.example-derived .env.

        Run out-of-process because app.core.config builds a module-level
        Settings() at import time; an already-imported module would hide the
        failure this test exists to catch.
        """
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(REPO_ROOT)!r})
            import main
            assert main.app is not None
            print("BOOT_OK")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(env_example_dir),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, f"importing main with .env.example failed:\n{result.stderr[-4000:]}"
        assert "BOOT_OK" in result.stdout

    def test_settings_errors_do_not_leak_values(self, monkeypatch):
        """
        A bad setting must not echo its value; pydantic's own ValidationError
        prints `input_value=...`, which for a .env file means secrets in
        tracebacks, CI logs and crash reports.
        """
        from app.core.config import SettingsError, get_settings

        secret = "s3cret-value-that-must-not-be-logged"
        monkeypatch.setenv("RATE_LIMIT_REQUESTS", secret)
        get_settings.cache_clear()
        try:
            with pytest.raises(SettingsError) as exc_info:
                get_settings()
            message = str(exc_info.value)
            assert secret not in message
            assert "RATE_LIMIT_REQUESTS" in message
        finally:
            get_settings.cache_clear()


class TestPlaceholderCredentialGuard:
    """
    The example file must not become a working production credential.

    .env.example ships a usable placeholder so `cp .env.example .env` boots.
    That is only safe because Settings refuses to run outside development
    while a placeholder is still in place.
    """

    def test_placeholder_key_rejected_outside_development(self, monkeypatch):
        """ENVIRONMENT=production with the example key must refuse to load."""
        from app.core.config import SettingsError, get_settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_KEYS", EXAMPLE_API_KEY)
        monkeypatch.setenv("SECRET_KEY", "a-real-generated-production-secret")
        get_settings.cache_clear()
        try:
            with pytest.raises(SettingsError) as exc_info:
                get_settings()
            assert "API_KEYS" in str(exc_info.value)
        finally:
            get_settings.cache_clear()

    def test_placeholder_single_api_key_rejected_outside_development(self, monkeypatch):
        """The API_KEY alias must be guarded exactly like API_KEYS."""
        from app.core.config import SettingsError, get_settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_KEYS", "")
        monkeypatch.setenv("API_KEY", EXAMPLE_API_KEY)
        monkeypatch.setenv("SECRET_KEY", "a-real-generated-production-secret")
        get_settings.cache_clear()
        try:
            with pytest.raises(SettingsError) as exc_info:
                get_settings()
            assert "API_KEY" in str(exc_info.value)
        finally:
            get_settings.cache_clear()

    def test_placeholder_secret_rejected_outside_development(self, monkeypatch):
        """ENVIRONMENT=production with the example SECRET_KEY must refuse."""
        from app.core.config import SettingsError, get_settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_KEYS", "a-real-generated-api-key")
        monkeypatch.setenv("SECRET_KEY", EXAMPLE_ENV["SECRET_KEY"])
        get_settings.cache_clear()
        try:
            with pytest.raises(SettingsError):
                get_settings()
        finally:
            get_settings.cache_clear()

    def test_real_credentials_accepted_in_production(self, monkeypatch):
        """Real generated secrets must boot in production."""
        from app.core.config import get_settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("API_KEYS", "a-real-generated-api-key")
        monkeypatch.setenv("SECRET_KEY", "a-real-generated-production-secret")
        get_settings.cache_clear()
        try:
            assert get_settings().API_KEYS == ["a-real-generated-api-key"]
        finally:
            get_settings.cache_clear()

    def test_placeholder_allowed_in_development(self, env_example_dir, monkeypatch):
        """The documented quickstart must still work as documented."""
        from app.core.config import Settings

        # os.environ wins over the .env file, and other test modules set
        # ENVIRONMENT without cleaning up; scrub it so this reads the file.
        for field_name in Settings.model_fields:
            monkeypatch.delenv(field_name, raising=False)
            monkeypatch.delenv(field_name.lower(), raising=False)

        settings = Settings(_env_file=str(env_example_dir / ".env"))
        assert settings.ENVIRONMENT == "development"
        assert settings.API_KEYS == [EXAMPLE_API_KEY]

    def test_installer_env_is_not_a_placeholder(self):
        """
        scripts/install.sh must generate real secrets, not copy the example.

        The installer sets ENVIRONMENT=production, so shipping a placeholder
        there would make every install fail to boot.
        """
        install_sh = (REPO_ROOT / "scripts" / "install.sh").read_text()
        assert "ENVIRONMENT=production" in install_sh
        assert "API_KEYS=${API_KEY}" in install_sh
        for placeholder in ("your-secure-api-key-here", "your-secure-secret-key"):
            assert placeholder not in install_sh


class TestListSettingsRoundTrip:
    """CRT-2: comma and JSON forms must both parse, for every list field."""

    @pytest.mark.parametrize(
        "field,raw,expected",
        [
            ("API_KEYS", "key1,key2", ["key1", "key2"]),
            ("API_KEYS", '["key1","key2"]', ["key1", "key2"]),
            ("API_KEYS", "solo", ["solo"]),
            ("API_KEYS", "", []),
            ("CORS_ORIGINS", "https://a.com", ["https://a.com"]),
            ("CORS_ORIGINS", "https://a.com,https://b.com", ["https://a.com", "https://b.com"]),
            ("CORS_ORIGINS", '["https://a.com","https://b.com"]', ["https://a.com", "https://b.com"]),
            ("CORS_METHODS", "GET,POST", ["GET", "POST"]),
            ("CORS_METHODS", '["GET","POST"]', ["GET", "POST"]),
            ("CORS_HEADERS", "X-API-Key, Content-Type", ["X-API-Key", "Content-Type"]),
            ("CORS_HEADERS", '["X-API-Key","Content-Type"]', ["X-API-Key", "Content-Type"]),
            ("SUSPICIOUS_PATTERNS", "<script,eval(", ["<script", "eval("]),
            ("SUSPICIOUS_PATTERNS", '["<script","eval("]', ["<script", "eval("]),
        ],
    )
    def test_list_field_accepts_both_formats(self, monkeypatch, field, raw, expected):
        """
        Before the fix, every comma-separated case raised SettingsError:
        pydantic-settings JSON-decodes List[str] fields BEFORE any
        mode="before" validator runs, so the assemble_* validators were dead
        code for their only real input source.
        """
        from app.core.config import Settings

        monkeypatch.setenv(field, raw)
        settings = Settings(_env_file=None)
        assert getattr(settings, field) == expected


class TestProtectedRouteAuth:
    """CRT-2/CRT-4: authentication behaviour on a protected route."""

    def test_valid_key_from_env_example_returns_200(self, app_with_env):
        """
        The key documented in .env.example must actually authenticate.

        Before the fix the app never loaded it (auth.py read os.getenv, which
        does not see values pydantic-settings read from .env) and every
        authenticated request returned 500.
        """
        client = _protected_client(app_with_env())
        response = client.get("/__boot_probe__", headers={"X-API-Key": EXAMPLE_API_KEY})
        assert response.status_code == 200, response.text
        assert response.json()["api_key"] == EXAMPLE_API_KEY

    def test_missing_header_returns_401(self, app_with_env):
        """No X-API-Key header must be 401 -- not 500, not 200."""
        client = _protected_client(app_with_env())
        response = client.get("/__boot_probe__")
        assert response.status_code == 401, response.text
        assert "detail" in response.json()

    def test_wrong_key_returns_401(self, app_with_env):
        """A wrong X-API-Key must be 401 -- not 500, not 200."""
        client = _protected_client(app_with_env())
        response = client.get("/__boot_probe__", headers={"X-API-Key": "definitely-not-the-key"})
        assert response.status_code == 401, response.text

    def test_empty_header_returns_401(self, app_with_env):
        """An empty X-API-Key must be 401, never treated as 'no auth needed'."""
        client = _protected_client(app_with_env())
        response = client.get("/__boot_probe__", headers={"X-API-Key": ""})
        assert response.status_code == 401, response.text

    def test_auth_disabled_allows_request_with_no_header(self, app_with_env):
        """
        CRT-4: with ENABLE_API_KEY_AUTH=false a request with NO header must
        succeed.

        Before the fix APIKeyHeader(auto_error=True) rejected the
        header-less request inside FastAPI, before the "auth disabled" branch
        could run -- so disabling auth inverted it: a probe looked locked
        while `X-API-Key: anything` returned 200 with real data.
        """
        client = _protected_client(app_with_env(ENABLE_API_KEY_AUTH="false"))
        response = client.get("/__boot_probe__")
        assert response.status_code == 200, response.text
        assert response.json()["api_key"] == "authentication-disabled"

    def test_auth_disabled_allows_arbitrary_header(self, app_with_env):
        """With auth off an arbitrary key is also accepted (consistently)."""
        client = _protected_client(app_with_env(ENABLE_API_KEY_AUTH="false"))
        response = client.get("/__boot_probe__", headers={"X-API-Key": "anything"})
        assert response.status_code == 200, response.text

    def test_no_keys_configured_still_rejects(self, app_with_env):
        """
        Fail closed: auth enabled with zero configured keys must reject every
        request, including one that supplies a key.
        """
        app = app_with_env(API_KEYS="", API_KEY="")
        client = _protected_client(app)
        assert client.get("/__boot_probe__").status_code == 401
        assert client.get("/__boot_probe__", headers={"X-API-Key": "guess"}).status_code == 500


class TestRealProtectedApiRoute:
    """The /api/v1 surface itself is gated, with the upstream mocked out."""

    def test_api_v1_route_requires_key(self, app_with_env):
        """A real /api/v1 route must 401 without a key, with no network call."""
        client = TestClient(app_with_env())
        response = client.get("/api/v1/google-news/search?query=test")
        assert response.status_code == 401, response.text

    def test_api_v1_route_passes_auth_with_valid_key(self, app_with_env, monkeypatch):
        """
        With the documented key from .env.example, a real /api/v1 route must
        return 200 -- not the 500 the broken auth produced for every
        authenticated request.

        The upstream GNews scraper is replaced, so no network access occurs
        and the status code reflects authentication only.
        """
        from app.api.google_news import google_news_api as news_api

        stub_article = {
            "title": "stub",
            "url": "https://example.invalid/a",
            "published_date": "2026-01-01T00:00:00Z",
            "description": "stub description",
            "publisher": "stub publisher",
        }

        class _FakeGNews:
            def get_news(self, _query):
                return [stub_article]

        async def _fake_instance(*_args, **_kwargs):
            return _FakeGNews()

        async def _fake_process(_news):
            return [stub_article]

        monkeypatch.setattr(news_api, "get_gnews_instance", _fake_instance)
        monkeypatch.setattr(news_api, "decode_and_process_articles", _fake_process)

        client = TestClient(app_with_env())
        response = client.get(
            "/api/v1/google-news/search?query=test",
            headers={"X-API-Key": EXAMPLE_API_KEY},
        )
        assert response.status_code == 200, response.text


def test_env_example_has_no_shell_interpolation():
    """
    .env.example must not use ${VAR}: python-dotenv expands it but
    `docker compose --env-file` passes it through literally, so the app and
    the containers would disagree about the database password.
    """
    offenders = [
        line for line in ENV_EXAMPLE.read_text().splitlines() if not line.strip().startswith("#") and "${" in line
    ]
    assert offenders == [], f"interpolation found in .env.example: {offenders}"


def test_env_example_covers_every_settings_field():
    """
    Every Settings field must appear in .env.example, so the documented
    configuration and the code cannot drift.
    """
    from app.core.config import Settings

    # Commented-out keys count as documented: REDIS_URL is deliberately unset
    # so the example file runs standalone (its compose hostname made every
    # local request 503), but it is still described in the file.
    documented = EXAMPLE_DOCUMENTED_KEYS
    # VERSION is derived from app/__version__.py and only overridden manually.
    expected = set(Settings.model_fields) - {"VERSION"}
    missing = sorted(expected - documented)
    assert missing == [], f"Settings fields missing from .env.example: {missing}"
