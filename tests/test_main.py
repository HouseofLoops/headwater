import contextlib

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI


#: Endpoints that disclose host metrics, dependency topology, rate-limit
#: thresholds or the effective configuration. None of them may be readable
#: without an API key.
SENSITIVE_ENDPOINTS = ["/health/detailed", "/status", "/api-config", "/config-sources"]

VALID_TEST_KEY = "unit-test-api-key"


@contextlib.contextmanager
def api_key_auth_enforced():
    """
    Force API key authentication on, deterministically.

    app.core.auth resolves its settings once at import time, and other test
    modules mutate ENABLE_API_KEY_AUTH in the process environment, so the
    effective auth mode depends on collection order. Pin every layer here so
    the security assertions below cannot pass or fail by accident.
    """
    import main
    from app.core import auth

    settings_stub = MagicMock()
    settings_stub.ENABLE_API_KEY_AUTH = True

    # auth resolves every decision through _auth_snapshot(), which rebuilds
    # itself whenever the Settings identity changes -- so patching the state
    # tuple directly would be discarded on the next call. Patch the accessor.
    auth_state = auth._AuthState(
        settings=settings_stub,
        keys=frozenset({VALID_TEST_KEY}),
        metadata={},
    )

    with patch.object(main, "get_settings", return_value=settings_stub), \
         patch.object(auth, "_auth_snapshot", return_value=auth_state):
        yield


class TestSensitiveEndpointAuthentication:
    """
    Regression tests for unauthenticated information disclosure.

    /health/detailed leaks host CPU, memory and disk plus dependency
    topology; /status leaks the running version and environment; /api-config
    leaks rate-limit thresholds and the CORS policy; /config-sources leaks
    how configuration is loaded. All of them used to be world-readable.
    """

    @pytest.fixture
    def app(self):
        from main import create_application
        return create_application()

    @pytest.mark.parametrize("path", SENSITIVE_ENDPOINTS)
    def test_sensitive_endpoint_rejects_missing_api_key(self, app, path):
        """No API key -> 401, and no payload at all."""
        with api_key_auth_enforced():
            response = TestClient(app).get(path)

        assert response.status_code == 401, (
            f"{path} is readable without an API key: {response.text[:200]}"
        )
        body = response.text.lower()
        for leak in ("cpu", "memory", "disk", "rate_limiting", "cors", "uptime"):
            assert leak not in body, f"{path} leaked {leak!r} in its 401 body"

    @pytest.mark.parametrize("path", SENSITIVE_ENDPOINTS)
    def test_sensitive_endpoint_rejects_invalid_api_key(self, app, path):
        """A wrong API key is rejected, not merely a missing one."""
        with api_key_auth_enforced():
            response = TestClient(app).get(path, headers={"X-API-Key": "wrong-key"})

        assert response.status_code == 401

    @patch('main.check_health', new_callable=AsyncMock)
    def test_sensitive_endpoint_accepts_valid_api_key(self, mock_check_health, app):
        """A valid API key still gets through - the gate is auth, not a block."""
        mock_check_health.return_value = {"status": "healthy", "details": {}}

        with api_key_auth_enforced():
            client = TestClient(app)
            for path in SENSITIVE_ENDPOINTS:
                response = client.get(path, headers={"X-API-Key": VALID_TEST_KEY})
                assert response.status_code == 200, f"{path} -> {response.status_code}"

    def test_liveness_endpoints_stay_public(self, app):
        """Load balancers must still be able to probe liveness without a key."""
        with api_key_auth_enforced():
            client = TestClient(app)
            assert client.get("/health").status_code == 200
            assert client.get("/ping").status_code == 200

    def test_public_health_discloses_liveness_only(self, app):
        """
        /health must not advertise the running version or the environment.

        Version disclosure on an unauthenticated endpoint tells an attacker
        exactly which known vulnerabilities to try.
        """
        with api_key_auth_enforced():
            response = TestClient(app).get("/health")

        assert response.json() == {"status": "healthy"}


class TestDocsExposure:
    """The OpenAPI schema and interactive docs are gated by environment."""

    def _app_for(self, environment):
        import main
        stub = MagicMock()
        stub.PROJECT_NAME = "Headwater"
        stub.DESCRIPTION = "desc"
        stub.VERSION = "1.2.0"
        stub.DEBUG = False
        stub.ENVIRONMENT = environment
        stub.CORS_ORIGINS = ["https://app.example.com"]
        stub.CORS_METHODS = ["GET"]
        stub.CORS_HEADERS = ["*"]
        with patch.object(main, "settings", stub):
            return main.create_application()

    def test_docs_served_outside_production(self):
        app = self._app_for("development")
        assert app.openapi_url == "/openapi.json"
        client = TestClient(app)
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/api/docs").status_code == 200

    def test_docs_not_served_in_production(self):
        """
        In production the API surface map is not published at all.

        404 rather than 401: a 401 would still confirm the routes exist.
        """
        app = self._app_for("production")
        assert app.openapi_url is None
        assert app.docs_url is None
        assert app.redoc_url is None

        # "localhost" so TrustedHostMiddleware (production-only) admits us.
        client = TestClient(app, base_url="http://localhost")
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/redoc").status_code == 404


class TestMainApplication:
    """Test the main FastAPI application setup and functionality."""

    @pytest.fixture
    def mock_settings(self):
        """Mock application settings."""
        settings = MagicMock()
        settings.PROJECT_NAME = "Headwater"
        settings.DESCRIPTION = "API for social media data aggregation and analysis"
        settings.VERSION = "1.2.0"
        settings.DEBUG = False  # Ensure debug is False for tests
        settings.ENVIRONMENT = "development"
        settings.RATE_LIMIT_ENABLED = True
        settings.RATE_LIMIT_REQUESTS = 100
        settings.RATE_LIMIT_TIMEFRAME = 60
        settings.ENABLE_CACHE = True
        settings.CACHE_TTL = 300
        settings.CORS_ORIGINS = ["*"]
        settings.CORS_METHODS = ["*"]
        settings.CORS_HEADERS = ["*"]
        settings.HOST = "0.0.0.0"
        settings.PORT = 8000
        return settings

    def test_create_application_basic(self, mock_settings):
        """Test basic application creation."""
        with patch('main.settings', mock_settings), \
             patch('main.configure_exception_handlers') as mock_configure_handlers, \
             patch('main.setup_middleware') as mock_setup_middleware, \
             patch('main.METRICS_AVAILABLE', False):

            from main import create_application
            app = create_application()

            assert isinstance(app, FastAPI)
            assert app.title == "Headwater"
            assert app.description == "API for social media data aggregation and analysis"
            assert app.version == "1.2.0"
            assert app.debug is False

            # Verify middleware and exception handlers were called
            mock_setup_middleware.assert_called_once()
            mock_configure_handlers.assert_called_once_with(app)

    def test_create_application_installs_rate_limit_middleware(self, mock_settings):
        """The real rate limiter must be installed as middleware.

        This previously asserted `app.state.limiter`, which was slowapi's.
        slowapi was in no requirements file, so that attribute proved only
        that a dead import had been attempted -- and slowapi keys by IP, the
        very defect app.core.rate_limiter exists to fix. The meaningful
        assertion is that RateLimitMiddleware is actually in the stack:
        Depends(rate_limit) covers only routes that declare it, so without
        the middleware /api-config, /status and /health count nothing.
        """
        from app.core.rate_limiter import RateLimitMiddleware

        with patch('main.settings', mock_settings), \
             patch('main.configure_exception_handlers'), \
             patch('main.METRICS_AVAILABLE', False):

            from main import create_application
            app = create_application()

            installed = [m.cls for m in app.user_middleware]
            assert RateLimitMiddleware in installed, (
                f"RateLimitMiddleware not installed; stack was {installed}"
            )

    def test_no_slowapi_limiter_remains(self):
        """The dead slowapi path must not come back.

        Re-adding it would install a second limiter keyed by remote address,
        reintroducing CRT-8 alongside the fixed limiter.
        """
        import main

        assert not hasattr(main, "limiter")
        assert not hasattr(main, "RATE_LIMITING_AVAILABLE")

    def test_create_application_with_metrics(self, mock_settings):
        """Test application creation with metrics enabled."""
        pytest.importorskip("prometheus_fastapi_instrumentator")

        mock_instrumentator = MagicMock()
        mock_instrumented_app = MagicMock()
        mock_instrumentator.instrument.return_value = mock_instrumented_app

        # main.py does `from prometheus_fastapi_instrumentator import
        # Instrumentator`, binding the name into main's namespace, so
        # patching the source module would not affect what main uses.
        with patch('main.settings', mock_settings), \
             patch('main.configure_exception_handlers'), \
             patch('main.setup_middleware'), \
             patch('main.METRICS_AVAILABLE', True), \
             patch('main.Instrumentator', return_value=mock_instrumentator):

            from main import create_application
            app = create_application()

            # Verify expose was called on the instrumented app
            mock_instrumented_app.expose.assert_called_once()
            kwargs = mock_instrumented_app.expose.call_args.kwargs
            assert mock_instrumented_app.expose.call_args.args == (app,)
            assert kwargs["endpoint"] == "/metrics"
            assert kwargs["include_in_schema"] is False

            # /metrics must be behind the API key: Prometheus' default
            # collectors publish process memory, start time, the Python
            # version and a labelled series per instrumented route.
            import main
            dependency_calls = kwargs["dependencies"]
            assert [dep.dependency for dep in dependency_calls] == [main.require_api_key]

    def test_metrics_expose_kwargs_are_accepted_by_instrumentator(self):
        """
        The dependencies kwarg must actually reach app.get().

        Instrumentator.expose forwards **kwargs to the route decorator; if a
        future version dropped that, create_application() would raise at
        startup rather than at test time.
        """
        instrumentator_module = pytest.importorskip("prometheus_fastapi_instrumentator")

        import inspect
        signature = inspect.signature(instrumentator_module.Instrumentator.expose)
        assert any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ), "Instrumentator.expose no longer forwards **kwargs to the route"

    @patch('main.stop_monitor_scheduler', new_callable=AsyncMock)
    @patch('main.start_monitor_scheduler')
    @patch('main.shutdown_rate_limiting', new_callable=AsyncMock)
    @patch('main.start_rate_limit_cleanup_task')
    @patch('main.shutdown_http_client_manager', new_callable=AsyncMock)
    @patch('main.setup_nltk', new_callable=AsyncMock)
    @patch('main.settings')
    def test_lifespan_startup_and_shutdown(
        self,
        mock_settings_patch,
        mock_setup_nltk,
        mock_shutdown_http,
        mock_start_cleanup,
        mock_shutdown_rate_limiting,
        mock_start_monitor_scheduler,
        mock_stop_monitor_scheduler,
        mock_settings,
    ):
        """
        The app must use the lifespan context manager, not @app.on_event.

        @app.on_event is deprecated and removed in newer Starlette, so this
        drives the app through TestClient's lifespan rather than walking
        app.router.on_startup (which is empty under lifespan).
        """
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        from main import create_application
        app = create_application()

        # The deprecated event hooks must be gone.
        assert app.router.on_startup == []
        assert app.router.on_shutdown == []

        with TestClient(app):
            # Startup ran
            mock_setup_nltk.assert_called_once()
            mock_start_cleanup.assert_called_once()
            mock_start_monitor_scheduler.assert_called_once()
            mock_shutdown_rate_limiting.assert_not_awaited()

        # Shutdown ran. shutdown_rate_limiting cancels AND awaits the janitor,
        # unlike stop_cleanup_task which only requests cancellation.
        mock_shutdown_rate_limiting.assert_awaited_once()
        mock_stop_monitor_scheduler.assert_awaited_once()
        mock_shutdown_http.assert_awaited_once()

    def test_rate_limit_cleanup_task_is_wired_into_lifespan(self):
        """
        The rate limiter's cleanup task must actually be started.

        Its in-memory store grows without bound otherwise; before this was
        wired in, start_cleanup_task() had zero callers anywhere in the tree.
        """
        import main
        from app.core import rate_limiter

        assert main.start_rate_limit_cleanup_task is rate_limiter.start_cleanup_task
        # Shutdown awaits the janitor rather than only cancelling it, so the
        # loop does not close with a pending task.
        assert main.shutdown_rate_limiting is rate_limiter.shutdown_rate_limiting

    def test_monitor_scheduler_is_wired_into_lifespan(self):
        """Maps monitors never fire unless the scheduler is started.

        create_monitor persists a record and reports status "active"; without
        a running scheduler nothing ever re-scrapes the place or delivers a
        webhook, so the monitor is active in name only.
        """
        import main
        from app.services import google_maps_monitors

        assert main.start_monitor_scheduler is google_maps_monitors.start_monitor_scheduler
        assert main.stop_monitor_scheduler is google_maps_monitors.stop_monitor_scheduler

    @patch('main.settings')
    def test_health_check_endpoints(self, mock_settings_patch, mock_settings):
        """Test health check endpoints."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        from main import create_application
        app = create_application()
        client = TestClient(app)

        # Basic health check: liveness only. Version and environment were
        # deliberately removed - see TestSensitiveEndpointAuthentication.
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

        # Test ping endpoint
        response = client.get("/ping")
        assert response.status_code == 200
        assert response.json() == {"ping": "pong"}

        # /status now requires an API key; its payload is asserted with a
        # valid key below.
        with api_key_auth_enforced():
            assert client.get("/status").status_code == 401

            data = client.get(
                "/status", headers={"X-API-Key": VALID_TEST_KEY}
            ).json()
        assert data["status"] == "online"
        assert data["version"] == "1.2.0"
        assert data["environment"] == "development"
        assert "timestamp" in data
        assert "uptime" in data

    @pytest.mark.asyncio
    @patch('main.check_health', new_callable=AsyncMock)
    @patch('main.settings')
    def test_detailed_health_check(self, mock_settings_patch, mock_check_health, mock_settings):
        """Test detailed health check endpoint."""
        mock_check_health.return_value = {"status": "healthy", "details": {}}
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        from main import create_application
        app = create_application()
        client = TestClient(app)

        with api_key_auth_enforced():
            response = client.get(
                "/health/detailed", headers={"X-API-Key": VALID_TEST_KEY}
            )
        assert response.status_code == 200

        # Verify check_health was called with correct parameters
        mock_check_health.assert_called_once()
        call_args = mock_check_health.call_args
        assert call_args[1]["include_details"] is True

    @patch('main.settings')
    def test_api_config_endpoint(self, mock_settings_patch, mock_settings):
        """Test API configuration endpoint."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT
        mock_settings_patch.RATE_LIMIT_ENABLED = mock_settings.RATE_LIMIT_ENABLED
        mock_settings_patch.RATE_LIMIT_REQUESTS = mock_settings.RATE_LIMIT_REQUESTS
        mock_settings_patch.RATE_LIMIT_TIMEFRAME = mock_settings.RATE_LIMIT_TIMEFRAME
        mock_settings_patch.ENABLE_CACHE = mock_settings.ENABLE_CACHE
        mock_settings_patch.CACHE_TTL = mock_settings.CACHE_TTL
        mock_settings_patch.CORS_ORIGINS = mock_settings.CORS_ORIGINS
        mock_settings_patch.CORS_METHODS = mock_settings.CORS_METHODS
        mock_settings_patch.CORS_HEADERS = mock_settings.CORS_HEADERS

        from main import create_application
        app = create_application()
        client = TestClient(app)

        with api_key_auth_enforced():
            response = client.get(
                "/api-config", headers={"X-API-Key": VALID_TEST_KEY}
            )
        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "Headwater"
        assert data["version"] == "1.2.0"
        assert data["environment"] == "development"
        assert "rate_limiting" in data
        assert "caching" in data
        assert "cors" in data

    @patch('main.settings')
    def test_config_sources_endpoint(self, mock_settings_patch, mock_settings):
        """Test configuration sources endpoint."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        from main import create_application
        app = create_application()
        client = TestClient(app)

        with api_key_auth_enforced():
            response = client.get(
                "/config-sources", headers={"X-API-Key": VALID_TEST_KEY}
            )
        assert response.status_code == 200
        data = response.json()

        assert "environment_variables" in data
        assert "env_file" in data
        assert "defaults" in data

    @patch('main.settings')
    def test_custom_docs_endpoints(self, mock_settings_patch, mock_settings):
        """Test custom documentation endpoints."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        from main import create_application
        app = create_application()
        client = TestClient(app)

        # Test custom swagger docs
        response = client.get("/api/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower()

        # Test custom redoc
        response = client.get("/api/redoc")
        assert response.status_code == 200
        assert "redoc" in response.text.lower()

    @patch('main.settings')
    def test_router_inclusion(self, mock_settings_patch, mock_settings):
        """
        Test that API routers are properly included.

        Asserted against the generated OpenAPI schema rather than
        app.routes. Since FastAPI 0.141 an included router is kept as a
        single wrapped _IncludedRouter entry, so walking app.routes only
        ever sees the ~13 top-level routes and never the mounted /api/v1
        paths - the old assertion failed even though the routers were
        mounted correctly. app.openapi() reflects what is actually served.
        """
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT
        mock_settings_patch.DESCRIPTION = mock_settings.DESCRIPTION

        from main import create_application
        app = create_application()

        paths = app.openapi()["paths"]
        api_routes = [path for path in paths if path.startswith("/api/v1")]
        assert api_routes, f"No API routes found. Available paths: {sorted(paths)}"

        # Every mounted sub-router must be represented.
        for prefix in (
            "/api/v1/google-news",
            "/api/v1/google-trends",
            "/api/v1/google-autocomplete",
            "/api/v1/youtube-transcripts",
            "/api/v1/google-maps",
        ):
            assert any(path.startswith(prefix) for path in paths), (
                f"Router {prefix} is not mounted"
            )

    @patch('main.settings')
    def test_app_state_initialization(self, mock_settings_patch, mock_settings):
        """Test that app state is properly initialized."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        from main import create_application
        app = create_application()

        # Check that start_time is set
        assert hasattr(app.state, 'start_time')
        assert isinstance(app.state.start_time, float)

    @patch('main.settings')
    def test_direct_execution(self, mock_settings_patch, mock_settings):
        """Test that the main module can be imported and has expected attributes."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT

        # Test that we can import the main module
        import main

        # Verify that the main module has the expected attributes
        assert hasattr(main, 'create_application')
        assert hasattr(main, 'app')

        # Verify that create_application is callable
        assert callable(main.create_application)

    @patch('main.settings')
    def test_openapi_schema_generation(self, mock_settings_patch, mock_settings):
        """Test OpenAPI schema generation."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT
        mock_settings_patch.DESCRIPTION = mock_settings.DESCRIPTION  # Add missing description

        from main import create_application
        app = create_application()

        # Test that OpenAPI schema can be generated
        schema = app.openapi()
        assert "info" in schema
        assert schema["info"]["title"] == "Headwater"
        assert schema["info"]["version"] == "1.2.0"

    @patch('main.settings')
    def test_cors_configuration(self, mock_settings_patch, mock_settings):
        """Test CORS configuration in API config."""
        # Configure the mock settings with the fixture values
        mock_settings_patch.PROJECT_NAME = mock_settings.PROJECT_NAME
        mock_settings_patch.VERSION = mock_settings.VERSION
        mock_settings_patch.ENVIRONMENT = mock_settings.ENVIRONMENT
        mock_settings_patch.RATE_LIMIT_ENABLED = mock_settings.RATE_LIMIT_ENABLED
        mock_settings_patch.RATE_LIMIT_REQUESTS = mock_settings.RATE_LIMIT_REQUESTS
        mock_settings_patch.RATE_LIMIT_TIMEFRAME = mock_settings.RATE_LIMIT_TIMEFRAME
        mock_settings_patch.ENABLE_CACHE = mock_settings.ENABLE_CACHE
        mock_settings_patch.CACHE_TTL = mock_settings.CACHE_TTL
        mock_settings_patch.CORS_ORIGINS = mock_settings.CORS_ORIGINS
        mock_settings_patch.CORS_METHODS = mock_settings.CORS_METHODS
        mock_settings_patch.CORS_HEADERS = mock_settings.CORS_HEADERS

        from main import create_application
        app = create_application()
        client = TestClient(app)

        with api_key_auth_enforced():
            response = client.get(
                "/api-config", headers={"X-API-Key": VALID_TEST_KEY}
            )
        assert response.status_code == 200
        data = response.json()

        cors_config = data["cors"]
        assert cors_config["origins"] == ["*"]
        assert cors_config["methods"] == ["*"]
        assert cors_config["headers"] == ["*"]
