# Headwater Scripts

This directory contains installation and management scripts for Headwater.

## Quick Install

```bash
# One-line install (requires root/sudo on Linux)
curl -fsSL https://raw.githubusercontent.com/rainmanjam/headwater/main/scripts/install.sh | sudo bash

# Or clone and run locally
git clone https://github.com/rainmanjam/headwater.git
cd headwater/scripts
sudo ./install.sh
```

## Scripts Overview

### install.sh

Main installation script that:
- Detects OS (Ubuntu/Debian, CentOS/RHEL/Fedora, macOS)
- Installs Docker and Docker Compose if needed
- Offers two installation modes:
  - **Docker Hub**: Pre-built images (recommended)
  - **GitHub Build**: Build from source
- Configures PostgreSQL, Redis, and the web API
- Optional SSL/HTTPS setup with Let's Encrypt
- Creates systemd service for auto-start
- Generates helper scripts

### Generated Helper Scripts

After installation, these scripts are created in `/opt/headwater/scripts/`:

| Script | Description |
|--------|-------------|
| `status.sh` | Check health and status of all services |
| `update.sh` | Update to latest version (creates backup first) |
| `backup.sh` | Create backup of database and configuration |
| `uninstall.sh` | Remove Headwater (with option to keep backups) |

## Configuration

The installer creates a `.env` file at `/opt/headwater/.env` with:

- `API_KEY` - API authentication key
- `POSTGRES_PASSWORD` - PostgreSQL password
- `REDIS_PASSWORD` - Redis password
- `SECRET_KEY` - Application secret key
- `WEB_PORT` - API port (default: 8000)

## Requirements

- Docker 20.10+
- Docker Compose 2.0+
- 2GB RAM minimum
- 10GB disk space

### Supported Operating Systems

- Ubuntu 20.04+, Debian 10+
- CentOS 8+, RHEL 8+, Fedora 35+
- macOS (requires Docker Desktop)

## Post-Installation

```bash
# Check status
/opt/headwater/scripts/status.sh

# View logs
docker compose -f /opt/headwater/docker-compose.yml logs -f

# Test API
curl -H "X-API-Key: YOUR_API_KEY" http://localhost:8000/health

# Access documentation
open http://localhost:8000/docs
```

## Troubleshooting

### Services not starting
```bash
# Check container logs
docker compose -f /opt/headwater/docker-compose.yml logs

# Restart services
docker compose -f /opt/headwater/docker-compose.yml restart
```

### Database connection issues
```bash
# Check PostgreSQL health
docker compose -f /opt/headwater/docker-compose.yml exec db pg_isready
```

### Permission issues on Linux
```bash
# Ensure you're running with sudo
sudo /opt/headwater/scripts/update.sh
```
