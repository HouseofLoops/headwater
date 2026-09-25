# Headwater

One self-hosted API for Google Maps, News, Trends and Autocomplete, plus YouTube
transcripts. Normalised JSON, no per-call vendor pricing.

**Source:** https://github.com/HouseofLoops/headwater · **License:** MIT

```bash
docker run -d --name headwater -p 8000:8000 \
  -e API_KEY=choose-a-long-random-string \
  rainmanjam/headwater:latest

curl -H "X-API-Key: choose-a-long-random-string" \
  "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=n8n"
```

```json
{"suggestions":["n8n","n8n workflows","n8n ai","n8n pricing","n8n github"]}
```

Interactive docs are at `/api/docs` once it is running. That list is generated
from the code, so it is always the authoritative one.

## Tags

| Tag | Architectures |
|---|---|
| `latest`, `2.2.3` | `linux/amd64`, `linux/arm64` |
| `2.0.0` | `linux/amd64`, `linux/arm64` |
| `1.5.1`, `1.5.0`, `1.4.1` | `linux/amd64`, `linux/arm64` |
| `1.6.0` | `linux/arm64` only |

`1.x` tags predate the rename from `social-flood` and are kept so older deploys
stay reproducible. They are byte-identical to the originals.

## Why

These five sources have no single official API between them. Getting search
interest, a place's reviews, a news feed and a video transcript into one
pipeline normally means four vendors, four auth schemes, four response shapes
and four invoices that scale per call.

Headwater is one service in front of all of them — 64 API operations behind one key
and one base URL, running on your own infrastructure with your own IP and your
own rate limits.

It is **not** a Google Cloud wrapper. Nothing here needs a Google API key, and
nothing here is an officially supported Google interface.

## Running it properly

Redis is not optional in production. Without it, Maps jobs, monitors and
webhooks fall back to in-memory storage that is lost on restart and invisible to
sibling workers. `/health/detailed` reports `record_storage_durable` so you can
assert on it rather than hope.

```yaml
services:
  headwater:
    image: rainmanjam/headwater:latest
    ports: ["8000:8000"]
    environment:
      API_KEY: choose-a-long-random-string
      REDIS_URL: redis://redis:6379/0
    depends_on: [redis]
  redis:
    image: redis:7-alpine
```

## Configuration

| Variable | Purpose |
|---|---|
| `API_KEY` | Required. Sent as `X-API-Key` on every request |
| `REDIS_URL` | Cache and durable record storage |
| `ENABLE_PROXY` / `PROXY_URLS` | Outbound proxying, comma-separated, round-robin |
| `NO_PROXY_HOSTS` | Hosts that bypass the proxy. Suffix match on a dot boundary |
| `NEWS_ARTICLE_ALLOWED_HOSTS` | Hosts `/article-details/` may fetch |
| `RATE_LIMIT_ENABLED` / `_REQUESTS` / `_TIMEFRAME` | Request throttling |
| `CORS_ORIGINS` | Explicit allow-list; a wildcard disables credentialed requests |

Proxying is per host rather than all-or-nothing, because the upstreams disagree:
Reddit answers `429` to datacentre IPs and needs a proxy, some providers refuse
`youtube.com` at the tunnel, and Google Maps loads fine through a plain `GET`
but a full browser navigation through a datacentre proxy never settles.

## What it covers

| Surface | Operations |
|---|---|
| Google Maps | 36 — search, nearby, grid and bounding-box, place details, reviews, photos, Q&A, popular times, async jobs, monitors, webhooks |
| Google Trends | 10 — interest over time and by region, related queries and topics, trending now, geo and category reference data |
| Google News | 9 — search, top, by topic, by source, by location, full-article extraction |
| YouTube transcripts | 5 — fetch, list, format, translate, batch |
| Google Autocomplete | 1 |

## Limits worth knowing before you adopt it

- **Maps costs about 12s per result**, by an order of magnitude the slowest
  thing here. Plan around the cache or use the async job endpoints.
- **These are unofficial interfaces.** Google changes markup and parameters
  without notice. The Maps scraper reports `selectors_stale` when extraction
  stops matching, so breakage surfaces as a signal rather than as silently empty
  results.
- **Upstream quotas are real.** Google Trends limits related queries and topics
  independently of anything configured here.
- **`summary` and `keywords` on `/article-details/` return `null`.** They needed
  nltk, which carries an unfixed advisory (PYSEC-2026-3740) with no patched
  release, so the dependency was dropped. Everything else — title, authors,
  publish date, full text, images — is unaffected.

## Image

Multi-stage build on `python:3.14-slim-trixie`, pinned by digest, running as a
non-root user, with Debian security updates applied at build time. Carries
standard `org.opencontainers.image.*` labels, so `docker inspect` tells you the
source, revision, version and license of whatever you pulled.

## Verifying images

Every release is signed by CI with keyless Cosign (Sigstore, GitHub OIDC) and
carries an SPDX SBOM attestation, on both `rainmanjam/headwater` and
`ghcr.io/houseofloops/headwater`. There is no public key; verify against the
signing workflow's identity:

- Certificate identity: `https://github.com/HouseofLoops/headwater/.github/workflows/release.yml@refs/heads/main` (2.2.2 and later)
  or `https://github.com/rainmanjam/headwater/.github/workflows/release.yml@refs/heads/main` (up to 2.2.1)
- OIDC issuer: `https://token.actions.githubusercontent.com`

> **Repository move.** Headwater moved from `rainmanjam` to the `HouseofLoops`
> GitHub organisation after 2.2.1. Each release keeps the identity it was signed
> with, so the examples below use `--certificate-identity-regexp` accepting
> exactly those two owners; `make docker-verify` does the same. GHCR images up to
> 2.2.1 remain at `ghcr.io/rainmanjam/headwater`; later ones are only at
> `ghcr.io/houseofloops/headwater`. Docker Hub (`rainmanjam/headwater`) is
> unchanged.

```bash
# Signature
cosign verify \
  --certificate-identity-regexp '^https://github\.com/(?i:rainmanjam|houseofloops)/headwater/\.github/workflows/release\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/houseofloops/headwater:<version>

# SPDX SBOM attestation
cosign verify-attestation --type spdxjson \
  --certificate-identity-regexp '^https://github\.com/(?i:rainmanjam|houseofloops)/headwater/\.github/workflows/release\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/houseofloops/headwater:<version>
```

Tags can move, so prefer pinning the digest (listed in each GitHub release, or
from `docker buildx imagetools inspect ghcr.io/houseofloops/headwater:<version>`):
replace `:<version>` above with `@sha256:<digest>`. From a clone of the repo,
`make docker-verify IMAGE=ghcr.io/houseofloops/headwater TAG=<version>` (or
`DIGEST=sha256:<digest>`) runs both checks.

Which cosign to use (tested against real images, keyless, on both registries):

| Release | Signature | SBOM attestation |
|---|---|---|
| 2.2.1 and later (signed with cosign 3) | cosign 2.6+ or 3.x | cosign 2.6+ or 3.x, `--type spdxjson` |
| 2.1.0, 2.2.0 (signed with cosign 2) | cosign 2.6+ or 3.x | **cosign 2.x only**, `--type spdx` or `spdxjson` |
| 2.0.0 and 1.x | not signed | none |

The 2.1.0/2.2.0 exception: their SBOM was attested with `--type spdx`, which
made cosign embed the SPDX JSON as a single string. cosign 3 requires the
predicate to be a JSON object and rejects it. From 2.2.1 the
SBOM is attested with `--type spdxjson`, as a real JSON object.

Full documentation, including deployment, performance tuning and troubleshooting
guides: https://github.com/HouseofLoops/headwater
