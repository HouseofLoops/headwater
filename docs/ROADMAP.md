# Roadmap

What Headwater does today, and what is not built yet. Release history is in
[CHANGELOG.md](./CHANGELOG.md); each release is published at
[GitHub Releases](https://github.com/HouseofLoops/headwater/releases).

## Current release: 2.2.2

- **Sources:** Google Maps (search, places, reviews, photos, Q&A, menus,
  directions, reservations, async jobs, monitors and webhooks), Google News,
  Google Trends, Google Autocomplete and YouTube transcripts: 64 API
  operations under one key and one base URL.
- **Operations:** Redis-backed caching, job and monitor storage; per-key rate
  limiting; per-host proxy routing; Prometheus metrics at `/metrics`;
  dependency-aware health checks.
- **Supply chain:** multi-arch images on Docker Hub and GHCR, signed keylessly
  with cosign and carrying an SPDX SBOM attestation, verified by the release
  itself before publishing.

## Open items

Each was checked against the code on 2026-09-23 and is not implemented yet.
Already done and dropped from this list: response compression, Prometheus
metrics, dependency-aware health checks, and webhooks for Google Maps monitors.

### Reliability and scale
- **Background task queue** for long-running work (batch transcripts, trends).
  Only Google Maps has async jobs today; there is no general queue (ARQ,
  Celery, Dramatiq).
- **Webhook on job completion.** `POST /api/v1/google-maps/webhooks` accepts
  `job.completed` / `job.failed` in `events`, but only `monitor.changed` is
  ever delivered, so Maps jobs must still be polled.
- **Circuit breakers** around upstream Google and YouTube calls.

### API
- **Cursor pagination** (`next_cursor` / `has_more`) for list endpoints.
- **Header-based version selection** (`X-API-Version`) alongside the URL prefix.
- **MCP server** at `/mcp` exposing a curated set of read-only tools to AI
  clients, mounted in the same app so it shares auth, caching and rate limits.
- **Generated client SDKs** from the OpenAPI schema.
- **GraphQL** query interface (low priority).

### Access control
- **Per-key rate-limit tiers.** There is one global `RATE_LIMIT_REQUESTS` /
  `RATE_LIMIT_TIMEFRAME` budget.
- **API key scopes** that restrict a key to endpoints or operations.
- **OAuth2 / JWT** user authentication in addition to API keys.
- **Inbound request signing** (HMAC). HMAC is used today only to sign outbound
  webhook deliveries.

### Observability
- **JSON structured logging.** Request IDs are propagated (`X-Request-ID`),
  but logs are plain text.
- **Distributed tracing** with OpenTelemetry.

### New data sources
- Twitter/X, Reddit (an early design is archived at
  [archive/reddit-intelligence-module.md](./archive/reddit-intelligence-module.md)),
  and keyword extraction.
