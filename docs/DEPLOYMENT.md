# Deployment Guide

How to run Headwater in development and production. Headwater is a single FastAPI
process listening on port 8000. Redis is optional but recommended; there is no
database.

## Prerequisites

| Need | Why |
|------|-----|
| Docker with Compose | Recommended path: runs the API and Redis together |
| Redis 7 (bundled in `docker-compose.yml`) | Shared rate-limit counters, cache and durable Maps jobs/monitors/webhooks |
| Outbound HTTPS to Google and YouTube | All data is fetched from public Google pages; no Google API key is used |
| An outbound proxy (optional) | Some upstreams throttle datacentre IPs; see `PROXY_URLS` below |

## Docker Compose with Redis (recommended)

```bash
git clone https://github.com/HouseofLoops/headwater.git
cd headwater
cp .env.example .env
# Edit .env: set API_KEYS, SECRET_KEY and REDIS_PASSWORD to real values
docker compose up -d
curl http://localhost:8000/health        # {"status":"healthy"}
```

`docker-compose.yml` defines two services:

| Service | Container | Notes |
|---------|-----------|-------|
| `web` | `headwater_app` | Built from the `Dockerfile`; port 8000; reads `.env` via `env_file`; health-checked with `curl -f http://localhost:8000/health` |
| `redis` | `headwater_redis` | `redis:7-alpine`, password from `REDIS_PASSWORD` (default `changeme`), AOF persistence in the `redis_data` volume; not published on the host |

Compose sets `REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379` for `web` itself, so
do not put a `redis` hostname in `.env` (it only resolves inside the Compose
network). Write full values in `.env`; `env_file` does no `${VAR}` expansion.

Compose builds the image locally. To run the published image instead, replace
`build: .` under `web` with `image: ghcr.io/houseofloops/headwater:<version>`.

Make shortcuts: `make docker-compose-up`, `make docker-compose-down`, `make logs`,
`make health-check`, `make check-env` (runs `scripts/check_env.py`).

## Published images

| Registry | Image |
|----------|-------|
| GitHub Container Registry | `ghcr.io/houseofloops/headwater` |
| Docker Hub | `rainmanjam/headwater` |

Tags are the release version (for example `2.2.2`) and `latest`. Images are built
by `.github/workflows/release.yml` for `linux/amd64` and `linux/arm64` from
`python:3.14-slim-trixie`, run as the non-root `appuser`, and include Playwright
Chromium for the Maps scraper.

Every release is signed keylessly with cosign (Sigstore, GitHub OIDC; there is no
signing key) and carries an SPDX SBOM attestation. Verify before deploying:

```bash
make docker-verify IMAGE=ghcr.io/houseofloops/headwater TAG=2.2.2
make docker-verify IMAGE=rainmanjam/headwater DIGEST=sha256:<digest>
```

`make docker-verify` needs cosign 2.6 or newer; signatures on releases after 2.2.0
use the cosign 3 bundle format, so use cosign 3. See [DOCKERHUB.md](DOCKERHUB.md)
for the manual `cosign verify` commands.

## Plain `docker run`

Without Redis, run exactly one worker (the image default):

```bash
docker run -d --name headwater -p 8000:8000 --env-file .env \
  ghcr.io/houseofloops/headwater:2.2.2
```

`make docker-run` does the same with a locally built `headwater` image
(`make docker-build`).

## Configuration

All settings are read by `app/core/config.py` from the environment or `.env`.
`.env.example` lists every one. The ones that matter for deployment:

| Variable | Default | Notes |
|----------|---------|-------|
| `API_KEYS` | `[]` | Accepted keys, comma-separated or JSON list. Clients send one in `X-API-Key` |
| `API_KEY` | unset | Single-key alias, merged into `API_KEYS` |
| `ENABLE_API_KEY_AUTH` | `true` | `false` makes every route public |
| `SECRET_KEY` | `development-secret-key-change-in-production` | Keys the digests that scope Maps jobs, monitors and webhooks to the API key that created them |
| `ENVIRONMENT` | `development` | See [Production mode](#production-mode) |
| `DEBUG` | `false` | |
| `REDIS_URL` | unset | Enables the shared rate-limit store, Redis cache and durable Maps records |
| `RATE_LIMIT_ENABLED` | `true` | |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_TIMEFRAME` | `100` / `3600` | Requests per window (seconds), per API key, or per client IP when no known key is sent |
| `UPSTREAM_RETRY_AFTER_SECONDS` | `60` | `Retry-After` sent with a 429 when Google rate-limits Headwater (Trends, News feeds, Autocomplete) and did not send its own |
| `ENABLE_CACHE` / `CACHE_TTL` | `true` / `3600` | Redis when `REDIS_URL` is set, in-process memory otherwise |
| `CORS_ORIGINS` | `["*"]` | Must be an explicit list in production |
| `ENABLE_PROXY` / `PROXY_URLS` | `false` / unset | Comma-separated proxies, rotated round-robin |
| `NO_PROXY_HOSTS` | unset | Hosts that always go direct (suffix match) |

Variables read directly from the process environment (not `Settings`):

| Variable | Default | Effect |
|----------|---------|--------|
| `RATE_LIMIT_FAIL_OPEN` | `false` | When the limiter backend is unreachable, let requests through (logged as errors) instead of returning 503 |
| `GOOGLE_MAPS_MAX_CONCURRENT_BROWSERS` | `4` | Concurrent Playwright browsers for Maps scraping |
| `GOOGLE_MAPS_MAX_FANOUT` | `25` | Ceiling on the points a grid or bulk Maps search actually visits |
| `MAPS_WEBHOOK_ALLOWED_HOSTS` | unset | Comma-separated webhook target hosts. Unset: any host, but it must resolve to public addresses |
| `NEWS_ARTICLE_ALLOWED_HOSTS` | unset | Comma-separated publisher hosts `/google-news/article-details/` may fetch (leading dot matches subdomains). Unset: Google News hosts only |
| `NEWS_ARTICLE_ALLOW_HTTP` | unset | `1`/`true`/`yes` allows plain `http` article URLs |

## Production mode

Setting `ENVIRONMENT=production` changes behaviour. Check these before the first
deploy:

| Behaviour | Where |
|-----------|-------|
| Startup fails if `API_KEYS`, `API_KEY` or `SECRET_KEY` still hold the `.env.example` placeholder (applies to any `ENVIRONMENT` other than `development`, `dev`, `local`, `test`, `testing`) | `app/core/config.py` |
| Startup fails if `CORS_ORIGINS` is the wildcard | `app/core/middleware.py` |
| Startup fails if rate limiting is on, more than one worker is configured and `REDIS_URL` is unset (also for `prod` and `staging`) | `app/core/rate_limiter.py` |
| `/docs`, `/redoc`, `/openapi.json`, `/api/docs` and `/api/redoc` are not served | `main.py` |
| A warning is logged if `ALLOWED_HOSTS` is `*` (the default). Set it to the hostnames you serve; any other Host then gets 400 (`localhost` and `127.0.0.1` stay allowed for health checks) | `app/core/middleware.py` |
| `Content-Security-Policy` and `Strict-Transport-Security` headers are added | `app/core/middleware.py` |

Other operational facts:

- The image runs `uvicorn main:app --workers 1`. To run more workers, set
  `REDIS_URL`; the in-memory limiter is per process.
- `/health` and `/ping` are unauthenticated and exempt from rate limiting, so
  health checks and probes can poll as often as they need. `/health/detailed`
  needs an API key and is rate limited.
- The limiter fails closed: if Redis is configured but unreachable, requests get
  503 unless `RATE_LIMIT_FAIL_OPEN=true`.
- Maps jobs, monitors and webhooks live in Redis when it is reachable and in
  memory otherwise. `GET /health/detailed` (authenticated) reports
  `record_storage_durable`.
- `/metrics` (Prometheus, via `prometheus-fastapi-instrumentator`) requires an API
  key like any other endpoint.

## Kubernetes (minimal example)

The repository ships no manifests. This is a correct starting point for this
app; add your own ingress and TLS.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: headwater
stringData:
  API_KEYS: "replace-with-a-real-key"
  SECRET_KEY: "replace-with-at-least-32-random-characters"
  REDIS_URL: "redis://:password@redis:6379"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: headwater
spec:
  replicas: 2
  selector:
    matchLabels: {app: headwater}
  template:
    metadata:
      labels: {app: headwater}
    spec:
      containers:
        - name: headwater
          image: ghcr.io/houseofloops/headwater:2.2.2
          ports:
            - containerPort: 8000
          env:
            - {name: ENVIRONMENT, value: production}
            - {name: CORS_ORIGINS, value: "https://app.example.com"}
            - {name: ALLOWED_HOSTS, value: "api.example.com"}
          envFrom:
            - secretRef: {name: headwater}
          livenessProbe:
            httpGet:
              path: /ping
              port: 8000
              httpHeaders: [{name: Host, value: localhost}]
            periodSeconds: 15
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
              httpHeaders: [{name: Host, value: localhost}]
            periodSeconds: 15
---
apiVersion: v1
kind: Service
metadata:
  name: headwater
spec:
  selector: {app: headwater}
  ports:
    - port: 80
      targetPort: 8000
```

Notes on the example:

- More than one replica needs `REDIS_URL` so that replicas share rate-limit
  counters and Maps records.
- The kubelet sends the pod IP as the Host header, which an explicit
  `ALLOWED_HOSTS` list rejects; `Host: localhost` is always allowed, hence the
  probe header. Your ingress must forward a Host that is in `ALLOWED_HOSTS`.
- Probes are exempt from rate limiting, so the interval is a normal 15 s.
- Run Redis however you prefer (a managed service or your own deployment).

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Container exits at startup with a placeholder-credential error | `.env.example` values left in place with a non-development `ENVIRONMENT` |
| Startup error about `CORS_ORIGINS` | Wildcard origins with `ENVIRONMENT=production` |
| Every request returns 400 "Invalid host header" | The request's Host is not in `ALLOWED_HOSTS` |
| Every request returns 503 | `REDIS_URL` set but Redis unreachable (limiter fails closed) |
| 401 on `/api/v1/...` | Missing or unknown `X-API-Key` |
| 500 "no API keys are configured" | `ENABLE_API_KEY_AUTH=true` with `API_KEYS` empty |

See also [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [SECURITY_GUIDELINES.md](SECURITY_GUIDELINES.md).
