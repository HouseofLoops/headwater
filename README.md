# Headwater API

A powerful API for accessing and aggregating data from various Google services including Google Maps, Google News, Google Trends, Google Autocomplete, and YouTube Transcripts.

[![Docker Hub](https://img.shields.io/docker/v/rainmanjam/headwater?label=Docker%20Hub&logo=docker)](https://hub.docker.com/r/rainmanjam/headwater)
[![GitHub release](https://img.shields.io/github/v/release/rainmanjam/headwater)](https://github.com/rainmanjam/headwater/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

- **Google Maps API** - Extract place data, reviews, photos, popular times, and live wait times
  - Grid-based geo-targeting for comprehensive area coverage
  - Bounding box coordinate search
  - Location name geocoding (city, ZIP, address)
- **Google News API** - Access and search news articles from Google News
- **Google Trends API** - Retrieve trending topics and search interest data
- **Google Autocomplete API** - Get search suggestions and keyword variations
- **YouTube Transcripts API** - Extract transcripts from YouTube videos
- **API Versioning** - All endpoints follow `/api/v1/` structure for future compatibility
- **RFC7807 Error Handling** - Standardized problem details for all error responses
- **Rate Limiting** - Configurable request throttling to prevent abuse
- **Comprehensive Health Checks** - Monitor system status and dependencies
- **Prometheus Metrics** - Track API usage and performance

## Quick Install

```bash
# One-line install (Linux/macOS)
curl -fsSL https://raw.githubusercontent.com/rainmanjam/headwater/main/scripts/install.sh | sudo bash
```

The installer will:
- Install Docker if not present
- Configure PostgreSQL and Redis
- Set up the API with secure defaults
- Optionally configure SSL/HTTPS with Let's Encrypt
- Create helper scripts (update, backup, uninstall)

## Getting Started

### Prerequisites

- Docker and Docker Compose
- (Optional) [Webshare Proxy](https://www.webshare.io/?referral_code=o116umkbm8da) for production scraping

### Manual Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/rainmanjam/headwater.git
   cd headwater
   ```

2. Copy the example environment file and configure your settings:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and configuration
   ```

3. Build and start the containers:
   ```bash
   docker-compose up -d
   ```

4. The API is now running at http://localhost:8000

### Docker Hub

Pull the pre-built image directly:

```bash
docker pull rainmanjam/headwater:latest
```

### API Documentation

- Swagger UI: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)
- ReDoc: [http://localhost:8000/api/redoc](http://localhost:8000/api/redoc)
- OpenAPI Schema: [http://localhost:8000/api/openapi.json](http://localhost:8000/api/openapi.json)

## Proxy Configuration

For production web scraping, we recommend using a proxy service to avoid rate limiting and IP blocks.

### Recommended: Webshare Proxy

[Webshare](https://www.webshare.io/?referral_code=o116umkbm8da) offers affordable, high-quality proxies perfect for Google Maps scraping:

| Feature | Details |
|---------|---------|
| **Free Tier** | 10 proxies, 1GB/month |
| **Datacenter Proxies** | Starting at $0.03/IP |
| **Residential Proxies** | Starting at $1.12/GB |
| **Static ISP Proxies** | Starting at $0.30/IP with unlimited bandwidth |

**Quick Setup:**

1. [Sign up for Webshare](https://www.webshare.io/?referral_code=o116umkbm8da) (free tier available)
2. Get your proxy credentials from the dashboard
3. Configure in your `.env` file:

```env
ENABLE_PROXY=true
# PROXY_URLS is comma-separated and rotated round-robin. Note the plural:
# the singular PROXY_URL is accepted as a legacy alias, but PROXY_URLS is
# what the code reads.
PROXY_URLS=http://username:password@proxy.webshare.io:80
# Several proxies, rotated per request:
PROXY_URLS=http://user:pass@proxy.webshare.io:80,http://user:pass@p.webshare.io:80
```

## Configuration

| Environment Variable | Description | Example |
|----------------------|-------------|---------|
| `API_KEYS` | Accepted API keys. Comma-separated **or** a JSON array | `key1,key2` or `["key1","key2"]` |
| `API_KEY` | Single-key alias for `API_KEYS` | `sf_abc123...` |
| `ENABLE_API_KEY_AUTH` | Enable/disable API key authentication | `true` |
| `RATE_LIMIT_ENABLED` | Enable/disable rate limiting | `true` |
| `RATE_LIMIT_REQUESTS` | Number of requests allowed per timeframe | `100` |
| `RATE_LIMIT_TIMEFRAME` | Timeframe for rate limiting in seconds | `3600` |
| `ENABLE_CACHE` | Enable/disable response caching | `true` |
| `CACHE_TTL` | Cache time-to-live in seconds | `3600` |
| `REDIS_URL` | Redis URL. Optional for a single-worker local run; **required** for multi-worker | `redis://localhost:6379/0` |
| `ENABLE_PROXY` | Enable/disable proxy for external requests | `false` |
| `PROXY_URLS` | Proxy URLs, comma-separated, rotated round-robin | `http://proxy:8080` |
| `ENVIRONMENT` | Application environment | `development` |
| `DEBUG` | Enable/disable debug mode | `false` |

See [.env.example](.env.example) for a complete list of configuration options.

### Configuration notes

- **List-valued settings** (`API_KEYS`, `CORS_ORIGINS`, `CORS_METHODS`,
  `CORS_HEADERS`, `SUSPICIOUS_PATTERNS`) accept either a comma-separated string
  or a JSON array.
- **Replace the placeholder values before setting `ENVIRONMENT` to anything
  other than `development`.** The app refuses to start in production on the
  placeholder API key or the default `SECRET_KEY`, rather than running on
  credentials published in this repository. `scripts/install.sh` generates real
  ones.
- **`.env` does not support `${VAR}` interpolation.** Docker Compose passes the
  file through verbatim, so a `${...}` reference is read as a literal string.
- **Redis is optional locally.** Without it the rate limiter uses an in-process
  store and caching falls back to memory. Because that store is per-process, a
  multi-worker deployment would silently multiply every limit by the worker
  count, so the app refuses to start in that configuration instead.
- **Rate limiting fails closed.** If the limiter cannot reach its backend,
  requests get `503` rather than passing unlimited. Set
  `RATE_LIMIT_FAIL_OPEN=true` to prefer availability over enforcement.

### Authentication and exposed endpoints

| Endpoint | Auth |
|----------|------|
| `/health`, `/ping` | Public. Liveness only — no version or environment. |
| `/health/detailed`, `/status`, `/api-config`, `/config-sources`, `/metrics` | **API key required** — they disclose host resources, dependency topology and configuration. |
| `/docs`, `/redoc`, `/openapi.json` | Served outside production; **not registered at all in production**. |
| All `/api/v1/*` | API key required. |

## Usage Examples

### Basic Health Check

```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "version": "1.6.0",
  "environment": "production",
  "timestamp": 1622548800.123456
}
```

### Google Maps Search

```bash
curl -X POST "http://localhost:8000/api/v1/google-maps/search" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "restaurants near Times Square",
    "max_results": 10,
    "language": "en"
  }'
```

### Google Maps Grid Search (Area Coverage)

```bash
curl -X POST "http://localhost:8000/api/v1/google-maps/grid-search" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "coffee shops",
    "center_lat": 40.7580,
    "center_lng": -73.9855,
    "radius_km": 2.0,
    "grid_size": 5,
    "max_results_per_point": 10
  }'
```

### Google News Search

```bash
curl -X GET "http://localhost:8000/api/v1/google-news/search?q=artificial+intelligence&country=US&language=en&max_results=5" \
  -H "X-API-Key: your_api_key"
```

### Google Autocomplete Suggestions

```bash
curl -X GET "http://localhost:8000/api/v1/google-autocomplete/autocomplete?q=python+programming&output=chrome&gl=US" \
  -H "X-API-Key: your_api_key"
```

For more examples, see the [docs/](docs/) folder.

## API Endpoints

### Google Maps
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/google-maps/search` | Search for places |
| POST | `/api/v1/google-maps/details` | Get place details by ID |
| POST | `/api/v1/google-maps/reviews` | Get place reviews |
| POST | `/api/v1/google-maps/grid-search` | Grid-based area search |
| POST | `/api/v1/google-maps/bounding-box-search` | Search within coordinates |
| POST | `/api/v1/google-maps/location-search` | Search by city/ZIP/address |

### Google News
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/google-news/search` | Search news articles |
| GET | `/api/v1/google-news/top/` | Get top headlines |
| GET | `/api/v1/google-news/topic/{topic}` | Get news by topic |

### Google Trends
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/google-trends/trending-now` | Get currently trending topics |
| GET | `/api/v1/google-trends/interest-over-time` | Get search interest data |

### Google Autocomplete
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/google-autocomplete/autocomplete` | Get search suggestions |

### YouTube Transcripts
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/youtube-transcripts/get-transcript` | Get video transcript |

## Documentation

- [docs/features/](docs/features/) - Feature documentation
- [docs/comparisons/](docs/comparisons/) - Service comparisons
- [scripts/README.md](scripts/README.md) - Installer and helper scripts

## Helper Scripts

After installation, these scripts are available:

```bash
# Check service status
/opt/headwater/scripts/status.sh

# Update to latest version
/opt/headwater/scripts/update.sh

# Create backup
/opt/headwater/scripts/backup.sh

# Uninstall
/opt/headwater/scripts/uninstall.sh
```

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=rainmanjam/headwater&type=Date)](https://star-history.com/#rainmanjam/headwater&Date)

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
