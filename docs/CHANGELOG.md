# Changelog

All notable changes to the Headwater API will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The repository moved to the `HouseofLoops` organisation**:
  https://github.com/HouseofLoops/headwater (old URLs redirect).
- **GHCR images are now published to `ghcr.io/houseofloops/headwater`.** Images
  up to 2.2.1 stay at `ghcr.io/rainmanjam/headwater`, which receives no new
  releases. Docker Hub (`rainmanjam/headwater`) is unchanged.
- Signer identity for new releases:
  `https://github.com/HouseofLoops/headwater/.github/workflows/release.yml@refs/heads/main`.
  Releases up to 2.2.1 keep the `rainmanjam` identity. `make docker-verify` and
  the documented commands accept both.

## [2.2.1] - 2026-09-23

Supply-chain release. The application code is the same as 2.2.0; only image
signing, the SBOM attestation, and verification tooling changed.

### Changed

- **Release signing moves to cosign 3.1.3** (from 2.6.5). Signatures use the
  Sigstore bundle format. Tested keylessly: they still verify with cosign 2.6+
  as well as 3.x.
- **The SBOM is attested with `--type spdxjson`**, not `spdx`. The old flag made
  cosign embed the SPDX JSON as one string. It is now a real JSON object that
  policy engines can query, under the same predicate type
  (`https://spdx.dev/Document`). The 2.1.0 and 2.2.0 SBOM attestations stay as
  they are and verify only with cosign 2.x.
- The release now **verifies its own signatures and attestations on both
  registries before publishing** the GitHub release.
- `make docker-verify` checks CI's keyless signature and SBOM without prompts.
  It needs cosign 2.6 or newer.

### Removed

- The key-based signing path: `make docker-sign`, `docker-sign-sbom`,
  `docker-sign-vuln`, `scripts/sign_image.sh` and `scripts/verify_attestations.sh`.
  It pointed at a private key that did not exist, its public key was never
  published, and no released image was ever signed with it.

### Added

- `.github/workflows/cosign-smoke.yml`: runs keyless sign, attest and verify on
  a throwaway image whenever the signing workflows change, and records what
  cosign 2.x can still verify.

## [2.2.0] - 2026-09-23

Releases 2.0.0 and 2.1.0 are described in their
[GitHub release notes](https://github.com/rainmanjam/headwater/releases).

### Fixed

- **Autocomplete `ds` filter was ignored.** `str()` of a `(str, Enum)` member is
  `DataSource.YOUTUBE` on Python 3.12+, and httpx sent that to Google instead of
  `yt`. All enums are now `StrEnum`. Autocomplete requests using `ds` get a new
  cache key, so they miss the cache once after upgrading.
- **YouTube transcript requests had no timeout.** A stalled connection held an
  executor thread forever. Requests now use `HTTP_CONNECTION_TIMEOUT` /
  `HTTP_READ_TIMEOUT`, a timeout is retried with proxy rotation, and a final
  timeout returns **504** (previously 500, or a misleading 502).
- **`/health/detailed` could return the Redis password.** A failed durability
  probe echoed the exception text, which can include `REDIS_URL`. It now logs the
  error and reports `"unknown"`.
- **Maps scrape jobs could stall in `pending`.** The background task had no
  strong reference and could be garbage-collected mid-run.
- A bare `except:` in optional API-key auth turned request cancellation into
  "no API key".
- `from app.schemas import ...` and `from app.services import *` raised
  `ImportError`.
- Photo URLs are matched by hostname (`*.googleusercontent.com`), not by
  substring.

### Security

- Request-derived values in logs go through `scrub()` at every call site that
  CodeQL flagged (19), in addition to the root-logger filter.
- bandit 1.8 could not parse Python 3.14 and silently passed every file; 1.9.4
  now scans for real, and its 13 findings are resolved.
- CodeQL: 0 open alerts (was 147). Gitleaks passes on full-history scans.

### Changed

- starlette 1.6.0 -> 1.7.0, uvicorn 0.52.4 -> 0.53.0, watchfiles 1.2.0 -> 1.3.0.
- Pydantic V2 migration completed (`field_validator`, `ConfigDict`,
  `min_length`/`max_length`). The OpenAPI schema is unchanged.
- FastAPI `Query(example=, regex=)` -> `examples=`, `pattern=`. The OpenAPI
  schema changes only in those example fields.
- Large modules split (Google Maps service/scraper, Google News, `core/utils`).
  All previous import paths still work.
- Lint and format are ruff only, and both block CI.
- cosign-installer v4, with cosign kept on 2.x so signatures verify as before.

## [1.1.0] - 2025-01-15

### Added

- **Comprehensive Performance Optimizations** across all API endpoints:
  - **Caching Infrastructure**: Implemented Redis/in-memory caching with configurable TTL for all endpoints
  - **HTTP Connection Pooling**: Added shared HTTP client pools with connection limits and timeouts
  - **Concurrent Processing**: Enhanced with asyncio.gather and semaphores for parallel operations
  - **Rate Limiting**: Integrated rate limiting middleware across all API endpoints
  - **Cache Key Generation**: Added consistent cache key generation functions for all APIs
  - **Input Sanitization**: Enhanced input validation and sanitization for security

### Performance Improvements

- **Google News API**: Added caching, concurrent URL decoding, shared HTTP clients, and rate limiting
- **Google Autocomplete API**: Implemented caching, rate limiting, HTTP connection pooling, and parallel processing
- **Google Trends API**: Added caching, rate limiting, and optimized connection management for all 10 endpoints
- **YouTube Transcripts API**: Enhanced with caching, rate limiting, and connection pooling for all 5 endpoints
- **NLTK Optimization**: Improved NLTK initialization and resource management
- **HTTP Manager**: Centralized HTTP client management with connection reuse

### Technical Enhancements

- **Cache Manager**: Unified caching interface with namespace support and TTL configuration
- **Rate Limiter**: Configurable request throttling with proper dependency injection
- **HTTP Client Pools**: Optimized connection management with keep-alive and timeout handling
- **Async Utilities**: Enhanced concurrent processing with proper error handling and resource limits

## [1.0.0] - 2025-05-31

### Added

- API versioning with `/api/v1/` prefix for all endpoints
- Comprehensive health check endpoints (`/health`, `/health/detailed`, `/ping`, `/status`)
- Configuration endpoints (`/api-config`, `/config-sources`)
- RFC7807 Problem Details for standardized error responses
- Prometheus metrics for monitoring (optional)
- Rate limiting with slowapi (optional)
- Custom OpenAPI documentation endpoints (`/api/docs`, `/api/redoc`)
- Proper startup and shutdown event handlers
- Comprehensive documentation (README, API_STRUCTURE, etc.)

### Changed

- Restructured main application with factory pattern
- Moved all API routers under versioned structure
- Updated URL paths for all endpoints
- Improved error handling with centralized exception handlers
- Enhanced middleware configuration
- Standardized router creation with BaseRouter

### Fixed

- Inconsistent naming in router tags
- Missing error handling for various scenarios
- Incomplete health checks
- Security headers configuration

## [0.2.0] - 2025-04-15

### Added

- Google Autocomplete API with comprehensive keyword variations
- YouTube Transcripts API for extracting video transcripts
- Proxy support for external API requests
- Caching layer with Redis support
- Basic health check endpoint

### Changed

- Improved error handling
- Enhanced logging configuration
- Updated dependencies

### Fixed

- Rate limiting issues
- Authentication edge cases

## [0.1.0] - 2025-03-01

### Added

- Initial release with Google News and Google Trends APIs
- Basic authentication with API keys
- Simple error handling
- Docker and Docker Compose support
- Basic documentation
