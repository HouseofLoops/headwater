# Security Guidelines

What Headwater does to protect itself, and what an operator has to do. Headwater
is self-hosted: you run it, you hold the keys, and there is no hosted service or
user account system behind it.

## Controls built into the app

| Control | Behaviour | Code |
|---------|-----------|------|
| API key auth | Every `/api/v1/*` route and `/status`, `/health/detailed`, `/api-config`, `/config-sources`, `/metrics` require a key in the `X-API-Key` header. 401 if missing or unknown; 500 if auth is on but no keys are configured. `ENABLE_API_KEY_AUTH=false` turns it off | `app/core/auth.py`, `main.py` |
| Unauthenticated routes | `/health` (returns only `{"status": "healthy"}`) and `/ping`; `/docs`, `/redoc`, `/openapi.json`, `/api/docs`, `/api/redoc` outside production only | `main.py` |
| Placeholder refusal | Startup fails outside development/test if `API_KEYS`, `API_KEY` or `SECRET_KEY` still hold the `.env.example` placeholder | `app/core/config.py` |
| Rate limiting | Per known API key, otherwise per client IP. `X-Forwarded-For` is ignored. Unknown keys do not get their own bucket. Fails closed (503) if the backend is unreachable unless `RATE_LIMIT_FAIL_OPEN=true` | `app/core/rate_limiter.py` |
| Rate-limit headers | `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (seconds until the window resets); 429 responses add `Retry-After` | `app/core/rate_limiter.py` |
| CORS | Wildcard origins are refused in production; with a wildcard, credentials are disabled | `app/core/middleware.py` |
| Security headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`; in production also `Content-Security-Policy` and `Strict-Transport-Security` | `app/core/middleware.py` |
| Trusted hosts | `ALLOWED_HOSTS` restricts the accepted Host headers (`localhost`/`127.0.0.1` always allowed). The default `*` accepts any host and logs a warning in production | `app/core/middleware.py` |
| SSRF defence | Caller-supplied URLs (`/google-news/article-details/`, Maps place lookup, Maps webhooks) are checked against a scheme and host allow-list, and every address the host resolves to must be public. The validated addresses are returned so callers can pin the connection against DNS rebinding | `app/core/url_guard.py` |
| Record ownership | Maps jobs, monitors and webhooks are scoped to the API key that created them, stored under a digest keyed with `SECRET_KEY` | `app/core/identity.py`, `app/services/record_store.py` |
| Input sanitisation | Autocomplete queries are length-limited (`MAX_QUERY_LENGTH`, default 200) and checked against `SUSPICIOUS_PATTERNS` when `BLOCK_SUSPICIOUS_PATTERNS=true` | `app/core/input_sanitizer.py` |
| Log injection | A logging filter escapes record separators so one log line cannot pose as several; call sites also wrap untrusted values with `scrub()` | `app/core/log_safety.py` |
| Error bodies | Errors are RFC 7807 `application/problem+json`; unhandled exceptions return a generic 500 without the exception text | `app/core/exceptions.py` |

## Operator checklist

- Generate real values for `API_KEYS` and `SECRET_KEY` (for example
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Keep them out
  of version control; `.env` is git-ignored.
- Give each client its own key in `API_KEYS` so one can be revoked by removing it
  and restarting.
- Set `ENVIRONMENT=production` and an explicit `CORS_ORIGINS` list.
- Set a strong `REDIS_PASSWORD` and do not publish the Redis port.
  `docker-compose.yml` keeps Redis on the internal network.
- Terminate TLS in a reverse proxy in front of port 8000. The proxy must send a
  Host header listed in `ALLOWED_HOSTS`, and you should set `ALLOWED_HOSTS` to
  the names you serve. If you rely on per-IP
  limits, make sure `request.client.host` is the real client (for example uvicorn
  `--proxy-headers --forwarded-allow-ips=<proxy address>`); otherwise all
  keyless clients share the proxy's bucket.
- Leave `ENABLE_API_KEY_AUTH=true` unless the service is reachable only from a
  trusted network.
- Set `MAPS_WEBHOOK_ALLOWED_HOSTS` if you can list your webhook receivers.
- Verify the image signature before deploying (`make docker-verify`, see
  [DEPLOYMENT.md](DEPLOYMENT.md#published-images)).

## Container

The published image (`Dockerfile`) is built from `python:3.14-slim-trixie`
pinned by digest, installs dependencies from the hash-pinned `requirements.lock`
(`pip install --require-hashes`), and runs as the non-root `appuser`. Chromium
for the Maps scraper is installed by Playwright into `/opt/playwright-browsers`.

## Supply chain and scanning

| Check | Where |
|-------|-------|
| Keyless cosign signature and SPDX SBOM attestation on every release | `.github/workflows/release.yml`; verify with `make docker-verify` |
| CodeQL, Trivy image and filesystem scans, dependency review | `.github/workflows/security.yml` |
| `pip-audit` | `.github/workflows/_verify.yml` |
| Dependabot for pip, GitHub Actions and Docker | `.github/dependabot.yml` |
| bandit, `detect-private-key` and other hooks | `.pre-commit-config.yaml` |

Run the same audit locally (`pip-audit` is pinned in `requirements-dev.txt`):

```bash
pip-audit --requirement requirements.txt --strict
pip-audit --requirement requirements.lock --no-deps --strict
```

## Reporting a vulnerability

Do not open a public issue with exploit details. Use GitHub's private
vulnerability reporting on the repository's Security tab
(https://github.com/HouseofLoops/headwater/security). If that is not available,
open an issue asking for a private contact and leave the details out.
