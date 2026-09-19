#!/bin/bash
#
# Headwater Installer
# Google Maps Data Extraction API
#
# This script installs Headwater with Docker Compose
# Supports: Ubuntu/Debian, CentOS/RHEL/Fedora, macOS
#
# Usage: curl -fsSL https://raw.githubusercontent.com/rainmanjam/headwater/main/scripts/install.sh | bash
#    or: ./install.sh
#

set -e

# =============================================================================
# Configuration
# =============================================================================

INSTALL_DIR="/opt/headwater"
GITHUB_REPO="https://github.com/rainmanjam/headwater.git"
DOCKER_IMAGE="rainmanjam/headwater"
MIN_DOCKER_VERSION="20.10.0"
MIN_COMPOSE_VERSION="2.0.0"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# State tracking
INSTALLATION_STAGE=""
CLEANUP_REQUIRED=false
DOCKER_INSTALLED=false
COMPOSE_INSTALLED=false

# =============================================================================
# Utility Functions
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${CYAN}==>${NC} ${GREEN}$1${NC}"
}

# Cleanup function for error handling
cleanup() {
    if [ "$CLEANUP_REQUIRED" = true ]; then
        log_warn "Installation failed at stage: $INSTALLATION_STAGE"
        log_warn "Cleaning up partial installation..."

        # Stop any running containers
        if [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
            cd "$INSTALL_DIR" 2>/dev/null && docker compose down 2>/dev/null || true
        fi

        log_info "Partial cleanup completed. You may need to manually remove:"
        log_info "  - Directory: $INSTALL_DIR"
        log_info "  - Docker volumes: docker volume ls | grep social"
    fi
}

trap cleanup EXIT

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Version comparison
version_gte() {
    # Returns 0 if $1 >= $2
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

# Generate random password
generate_password() {
    local length=${1:-32}
    if command_exists openssl; then
        openssl rand -base64 $length | tr -dc 'a-zA-Z0-9' | head -c $length
    elif [ -f /dev/urandom ]; then
        cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c $length
    else
        date +%s | sha256sum | base64 | head -c $length
    fi
}

# Generate API key
generate_api_key() {
    local prefix=${1:-"sf"}
    echo "${prefix}_$(generate_password 24)"
}

# Detect OS
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
        OS_NAME=$PRETTY_NAME
    elif [ -f /etc/redhat-release ]; then
        OS="rhel"
        OS_VERSION=$(cat /etc/redhat-release | grep -oE '[0-9]+' | head -1)
        OS_NAME=$(cat /etc/redhat-release)
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        OS_VERSION=$(sw_vers -productVersion)
        OS_NAME="macOS $OS_VERSION"
    else
        OS="unknown"
        OS_VERSION="unknown"
        OS_NAME="Unknown OS"
    fi

    log_info "Detected OS: $OS_NAME"
}

# Check if running as root (or with sudo)
check_privileges() {
    if [ "$EUID" -ne 0 ] && [ "$OS" != "macos" ]; then
        log_error "This script must be run as root or with sudo"
        log_info "Please run: sudo $0"
        exit 1
    fi
}

# =============================================================================
# Docker Installation Functions
# =============================================================================

install_docker_debian() {
    log_step "Installing Docker on Debian/Ubuntu..."

    # Remove old versions
    apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

    # Install prerequisites
    apt-get update
    apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    # Set up repository
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS \
        $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Install Docker
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Start and enable Docker
    systemctl start docker
    systemctl enable docker

    DOCKER_INSTALLED=true
    COMPOSE_INSTALLED=true
}

install_docker_rhel() {
    log_step "Installing Docker on RHEL/CentOS/Fedora..."

    # Remove old versions
    dnf remove -y docker docker-client docker-client-latest docker-common \
        docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true

    # Install prerequisites
    dnf install -y dnf-plugins-core

    # Add Docker repository
    dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo

    # Install Docker
    dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Start and enable Docker
    systemctl start docker
    systemctl enable docker

    DOCKER_INSTALLED=true
    COMPOSE_INSTALLED=true
}

install_docker_macos() {
    log_step "Checking Docker on macOS..."

    if ! command_exists docker; then
        log_error "Docker Desktop is not installed"
        log_info "Please install Docker Desktop from: https://www.docker.com/products/docker-desktop/"
        log_info "After installation, run this script again"
        exit 1
    fi

    # Check if Docker is running
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker Desktop is not running"
        log_info "Please start Docker Desktop and run this script again"
        exit 1
    fi

    DOCKER_INSTALLED=true
    COMPOSE_INSTALLED=true
    log_success "Docker Desktop is installed and running"
}

install_docker() {
    INSTALLATION_STAGE="docker_installation"
    CLEANUP_REQUIRED=true

    # Check if Docker is already installed
    if command_exists docker; then
        local docker_version=$(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        if version_gte "$docker_version" "$MIN_DOCKER_VERSION"; then
            log_success "Docker $docker_version is already installed"
            DOCKER_INSTALLED=true
        else
            log_warn "Docker $docker_version is below minimum required version $MIN_DOCKER_VERSION"
            log_info "Will attempt to upgrade Docker"
        fi
    fi

    # Check if Docker Compose is available
    if docker compose version >/dev/null 2>&1; then
        local compose_version=$(docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        if version_gte "$compose_version" "$MIN_COMPOSE_VERSION"; then
            log_success "Docker Compose $compose_version is available"
            COMPOSE_INSTALLED=true
        fi
    fi

    # Install if needed
    if [ "$DOCKER_INSTALLED" = false ] || [ "$COMPOSE_INSTALLED" = false ]; then
        case "$OS" in
            ubuntu|debian)
                install_docker_debian
                ;;
            centos|rhel|fedora|rocky|alma)
                install_docker_rhel
                ;;
            macos)
                install_docker_macos
                ;;
            *)
                log_error "Unsupported OS: $OS"
                log_info "Please install Docker manually and run this script again"
                exit 1
                ;;
        esac
    fi

    # Verify installation
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker installation failed or Docker daemon is not running"
        exit 1
    fi

    log_success "Docker is ready"
}

# =============================================================================
# Installation Source Selection
# =============================================================================

select_installation_source() {
    log_step "Select Installation Source"
    echo ""
    echo "How would you like to install Headwater?"
    echo ""
    echo "  1) Docker Hub (Recommended)"
    echo "     - Pre-built image, faster installation"
    echo "     - Stable releases only"
    echo ""
    echo "  2) Build from GitHub"
    echo "     - Build from source code"
    echo "     - Access to latest development features"
    echo ""

    while true; do
        read -p "Select option [1-2] (default: 1): " source_choice
        source_choice=${source_choice:-1}

        case $source_choice in
            1)
                INSTALL_SOURCE="dockerhub"
                log_info "Using Docker Hub image"
                break
                ;;
            2)
                INSTALL_SOURCE="github"
                log_info "Building from GitHub source"
                break
                ;;
            *)
                log_warn "Invalid option. Please enter 1 or 2"
                ;;
        esac
    done
}

# =============================================================================
# Configuration Gathering
# =============================================================================

gather_configuration() {
    INSTALLATION_STAGE="configuration"
    log_step "Configuration Setup"
    echo ""

    # Install directory
    read -p "Installation directory [$INSTALL_DIR]: " input_dir
    INSTALL_DIR=${input_dir:-$INSTALL_DIR}
    log_info "Install directory: $INSTALL_DIR"

    # API Key
    echo ""
    log_info "API Key Configuration"
    echo "The API key is used to authenticate requests to Headwater"
    default_api_key=$(generate_api_key)
    read -p "API Key (leave blank to generate): " input_api_key
    API_KEY=${input_api_key:-$default_api_key}

    # Database passwords
    echo ""
    log_info "Database Configuration"
    default_pg_password=$(generate_password 24)
    read -p "PostgreSQL password (leave blank to generate): " input_pg_password
    POSTGRES_PASSWORD=${input_pg_password:-$default_pg_password}

    default_redis_password=$(generate_password 24)
    read -p "Redis password (leave blank to generate): " input_redis_password
    REDIS_PASSWORD=${input_redis_password:-$default_redis_password}

    # Secret key
    default_secret_key=$(generate_password 48)
    SECRET_KEY=$default_secret_key

    # Web port
    echo ""
    log_info "Network Configuration"
    read -p "Web port [8000]: " input_port
    WEB_PORT=${input_port:-8000}

    # SSL Configuration
    echo ""
    read -p "Configure SSL/HTTPS with Let's Encrypt? [y/N]: " ssl_choice
    if [[ "$ssl_choice" =~ ^[Yy]$ ]]; then
        SETUP_SSL=true
        read -p "Domain name (e.g., api.example.com): " DOMAIN_NAME
        read -p "Email for Let's Encrypt: " LETSENCRYPT_EMAIL
    else
        SETUP_SSL=false
    fi

    echo ""
    log_success "Configuration gathered"
}

# =============================================================================
# Installation Functions
# =============================================================================

create_directories() {
    INSTALLATION_STAGE="directory_creation"
    log_step "Creating installation directories..."

    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/data/postgres"
    mkdir -p "$INSTALL_DIR/data/redis"
    mkdir -p "$INSTALL_DIR/logs"
    mkdir -p "$INSTALL_DIR/backups"
    mkdir -p "$INSTALL_DIR/scripts"

    log_success "Directories created"
}

clone_repository() {
    INSTALLATION_STAGE="repository_clone"
    log_step "Cloning Headwater repository..."

    if command_exists git; then
        git clone "$GITHUB_REPO" "$INSTALL_DIR/source"
    else
        log_info "Git not found, downloading archive..."
        curl -fsSL "https://github.com/rainmanjam/headwater/archive/main.tar.gz" | tar -xz -C "$INSTALL_DIR"
        mv "$INSTALL_DIR/headwater-main" "$INSTALL_DIR/source"
    fi

    log_success "Repository cloned"
}

create_env_file() {
    INSTALLATION_STAGE="env_creation"
    log_step "Creating environment configuration..."

    cat > "$INSTALL_DIR/.env" << EOF
# Headwater Configuration
# Generated by installer on $(date)

# API Configuration
# API_KEYS is the variable app/core/config.py reads (comma-separated list).
# API_KEY is kept as a single-key alias for backward compatibility.
API_KEYS=${API_KEY}
ENABLE_API_KEY_AUTH=true
SECRET_KEY=${SECRET_KEY}
DEBUG=false
ENVIRONMENT=production

# PostgreSQL Configuration
POSTGRES_USER=headwater
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=headwater
POSTGRES_HOST=db
POSTGRES_PORT=5432
DATABASE_URL=postgresql://headwater:${POSTGRES_PASSWORD}@db:5432/headwater

# Redis Configuration
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0

# Web Server Configuration
WEB_PORT=${WEB_PORT}
HOST=0.0.0.0
PORT=8000
WORKERS=4

# Scraping Configuration
SCRAPER_TIMEOUT=30000
SCRAPER_MAX_RETRIES=3
HEADLESS=true

# Rate Limiting
# RATE_LIMIT_TIMEFRAME (seconds) is the name Settings reads, not
# RATE_LIMIT_WINDOW -- the old name was silently ignored.
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_TIMEFRAME=3600

# Logging
LOG_LEVEL=INFO
EOF

    chmod 600 "$INSTALL_DIR/.env"
    log_success "Environment file created"
}

create_docker_compose() {
    INSTALLATION_STAGE="docker_compose_creation"
    log_step "Creating Docker Compose configuration..."

    if [ "$INSTALL_SOURCE" = "github" ]; then
        # Build from source
        cat > "$INSTALL_DIR/docker-compose.yml" << 'EOF'
version: '3.8'

services:
  web:
    build:
      context: ./source
      dockerfile: Dockerfile
    container_name: headwater-web
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-8000}:8000"
    environment:
      # API_KEYS is the variable app/core/config.py reads (comma-separated).
      - API_KEYS=${API_KEYS:-${API_KEY}}
      - ENABLE_API_KEY_AUTH=${ENABLE_API_KEY_AUTH:-true}
      - SECRET_KEY=${SECRET_KEY}
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - DEBUG=${DEBUG:-false}
      - HEADLESS=${HEADLESS:-true}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs
    networks:
      - headwater-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  db:
    image: postgres:16-alpine
    container_name: headwater-db
    restart: unless-stopped
    environment:
      - POSTGRES_USER=${POSTGRES_USER:-headwater}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB:-headwater}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    networks:
      - headwater-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-headwater}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: headwater-redis
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - ./data/redis:/data
    networks:
      - headwater-network
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

networks:
  headwater-network:
    driver: bridge
EOF
    else
        # Use Docker Hub image
        cat > "$INSTALL_DIR/docker-compose.yml" << 'EOF'
version: '3.8'

services:
  web:
    image: rainmanjam/headwater:latest
    container_name: headwater-web
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-8000}:8000"
    environment:
      # API_KEYS is the variable app/core/config.py reads (comma-separated).
      - API_KEYS=${API_KEYS:-${API_KEY}}
      - ENABLE_API_KEY_AUTH=${ENABLE_API_KEY_AUTH:-true}
      - SECRET_KEY=${SECRET_KEY}
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - DEBUG=${DEBUG:-false}
      - HEADLESS=${HEADLESS:-true}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs
    networks:
      - headwater-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  db:
    image: postgres:16-alpine
    container_name: headwater-db
    restart: unless-stopped
    environment:
      - POSTGRES_USER=${POSTGRES_USER:-headwater}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB:-headwater}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    networks:
      - headwater-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-headwater}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: headwater-redis
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - ./data/redis:/data
    networks:
      - headwater-network
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

networks:
  headwater-network:
    driver: bridge
EOF
    fi

    log_success "Docker Compose file created"
}

setup_ssl() {
    if [ "$SETUP_SSL" != true ]; then
        return
    fi

    INSTALLATION_STAGE="ssl_setup"
    log_step "Setting up SSL with Let's Encrypt..."

    # Install certbot if not present
    if ! command_exists certbot; then
        case "$OS" in
            ubuntu|debian)
                apt-get install -y certbot
                ;;
            centos|rhel|fedora|rocky|alma)
                dnf install -y certbot
                ;;
            *)
                log_warn "Please install certbot manually for SSL support"
                return
                ;;
        esac
    fi

    # Create nginx config for SSL
    mkdir -p "$INSTALL_DIR/nginx"

    cat > "$INSTALL_DIR/nginx/nginx.conf" << EOF
events {
    worker_connections 1024;
}

http {
    upstream headwater {
        server web:8000;
    }

    server {
        listen 80;
        server_name ${DOMAIN_NAME};

        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }

        location / {
            return 301 https://\$host\$request_uri;
        }
    }

    server {
        listen 443 ssl http2;
        server_name ${DOMAIN_NAME};

        ssl_certificate /etc/letsencrypt/live/${DOMAIN_NAME}/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/${DOMAIN_NAME}/privkey.pem;

        ssl_session_timeout 1d;
        ssl_session_cache shared:SSL:50m;
        ssl_session_tickets off;

        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;

        location / {
            proxy_pass http://headwater;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
}
EOF

    # Add nginx service to docker-compose
    cat >> "$INSTALL_DIR/docker-compose.yml" << EOF

  nginx:
    image: nginx:alpine
    container_name: headwater-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certbot/conf:/etc/letsencrypt
      - ./certbot/www:/var/www/certbot
    depends_on:
      - web
    networks:
      - headwater-network
EOF

    # Obtain certificate
    mkdir -p "$INSTALL_DIR/certbot/conf"
    mkdir -p "$INSTALL_DIR/certbot/www"

    log_info "Obtaining SSL certificate for $DOMAIN_NAME..."
    certbot certonly --standalone -d "$DOMAIN_NAME" --email "$LETSENCRYPT_EMAIL" --agree-tos --non-interactive || {
        log_warn "SSL certificate generation failed. You can retry later with:"
        log_info "certbot certonly --standalone -d $DOMAIN_NAME"
    }

    log_success "SSL configuration completed"
}

# =============================================================================
# Helper Scripts Generation
# =============================================================================

create_update_script() {
    log_info "Creating update script..."

    cat > "$INSTALL_DIR/scripts/update.sh" << 'EOF'
#!/bin/bash
#
# Headwater Update Script
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

cd "$INSTALL_DIR"

echo "==> Updating Headwater..."

# Create backup before update
if [ -f "$INSTALL_DIR/scripts/backup.sh" ]; then
    echo "Creating backup before update..."
    bash "$INSTALL_DIR/scripts/backup.sh"
fi

# Pull latest images
echo "Pulling latest images..."
docker compose pull

# Update from source if applicable
if [ -d "$INSTALL_DIR/source" ]; then
    echo "Updating source code..."
    cd "$INSTALL_DIR/source"
    git pull origin main
    cd "$INSTALL_DIR"
fi

# Recreate containers
echo "Recreating containers..."
docker compose up -d --build

# Cleanup old images
echo "Cleaning up old images..."
docker image prune -f

echo "==> Update completed successfully!"
echo ""
echo "Check status with: docker compose -f $INSTALL_DIR/docker-compose.yml ps"
EOF

    chmod +x "$INSTALL_DIR/scripts/update.sh"
}

create_uninstall_script() {
    log_info "Creating uninstall script..."

    cat > "$INSTALL_DIR/scripts/uninstall.sh" << 'EOF'
#!/bin/bash
#
# Headwater Uninstall Script
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Headwater Uninstall"
echo ""
echo "This will remove Headwater and all its data."
echo "Installation directory: $INSTALL_DIR"
echo ""

read -p "Are you sure you want to uninstall? [y/N]: " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Uninstall cancelled."
    exit 0
fi

read -p "Do you want to keep backups? [Y/n]: " keep_backups

cd "$INSTALL_DIR"

echo "Stopping containers..."
docker compose down -v

echo "Removing containers..."
docker compose rm -f

# Remove images
echo "Removing Docker images..."
docker rmi headwater-web 2>/dev/null || true
docker rmi rainmanjam/headwater 2>/dev/null || true

# Handle backups
if [[ ! "$keep_backups" =~ ^[Nn]$ ]] && [ -d "$INSTALL_DIR/backups" ] && [ "$(ls -A $INSTALL_DIR/backups)" ]; then
    backup_dest="$HOME/headwater-backups"
    echo "Moving backups to $backup_dest..."
    mkdir -p "$backup_dest"
    mv "$INSTALL_DIR/backups"/* "$backup_dest/"
fi

# Remove installation directory
echo "Removing installation directory..."
cd /
rm -rf "$INSTALL_DIR"

# Remove systemd service if exists
if [ -f /etc/systemd/system/headwater.service ]; then
    echo "Removing systemd service..."
    systemctl stop headwater 2>/dev/null || true
    systemctl disable headwater 2>/dev/null || true
    rm -f /etc/systemd/system/headwater.service
    systemctl daemon-reload
fi

echo ""
echo "==> Headwater has been uninstalled."
if [[ ! "$keep_backups" =~ ^[Nn]$ ]]; then
    echo "Backups preserved at: $backup_dest"
fi
EOF

    chmod +x "$INSTALL_DIR/scripts/uninstall.sh"
}

create_backup_script() {
    log_info "Creating backup script..."

    cat > "$INSTALL_DIR/scripts/backup.sh" << 'EOF'
#!/bin/bash
#
# Headwater Backup Script
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$INSTALL_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="headwater-backup-$TIMESTAMP"

cd "$INSTALL_DIR"

echo "==> Creating Headwater Backup"
echo "Backup name: $BACKUP_NAME"

mkdir -p "$BACKUP_DIR/$BACKUP_NAME"

# Backup PostgreSQL database
echo "Backing up PostgreSQL database..."
docker compose exec -T db pg_dump -U headwater headwater > "$BACKUP_DIR/$BACKUP_NAME/database.sql"

# Backup Redis data
echo "Backing up Redis data..."
docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD:-password}" BGSAVE
sleep 2
cp -r "$INSTALL_DIR/data/redis" "$BACKUP_DIR/$BACKUP_NAME/redis-data" 2>/dev/null || true

# Backup configuration
echo "Backing up configuration..."
cp "$INSTALL_DIR/.env" "$BACKUP_DIR/$BACKUP_NAME/env.backup"
cp "$INSTALL_DIR/docker-compose.yml" "$BACKUP_DIR/$BACKUP_NAME/docker-compose.yml.backup"

# Create archive
echo "Creating backup archive..."
cd "$BACKUP_DIR"
tar -czf "$BACKUP_NAME.tar.gz" "$BACKUP_NAME"
rm -rf "$BACKUP_NAME"

# Cleanup old backups (keep last 7)
echo "Cleaning up old backups..."
ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm

echo ""
echo "==> Backup completed: $BACKUP_DIR/$BACKUP_NAME.tar.gz"
EOF

    chmod +x "$INSTALL_DIR/scripts/backup.sh"
}

create_status_script() {
    log_info "Creating status script..."

    cat > "$INSTALL_DIR/scripts/status.sh" << 'EOF'
#!/bin/bash
#
# Headwater Status Script
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

cd "$INSTALL_DIR"

echo "==> Headwater Status"
echo ""

# Container status
echo "Container Status:"
echo "-----------------"
docker compose ps
echo ""

# Health checks
echo "Health Checks:"
echo "--------------"
WEB_PORT=$(grep -E "^WEB_PORT=" "$INSTALL_DIR/.env" | cut -d'=' -f2)
WEB_PORT=${WEB_PORT:-8000}

echo -n "Web API: "
if curl -sf "http://localhost:$WEB_PORT/health" > /dev/null 2>&1; then
    echo "✓ Healthy"
else
    echo "✗ Unhealthy"
fi

echo -n "Database: "
if docker compose exec -T db pg_isready -U headwater > /dev/null 2>&1; then
    echo "✓ Ready"
else
    echo "✗ Not Ready"
fi

echo -n "Redis: "
if docker compose exec -T redis redis-cli ping > /dev/null 2>&1; then
    echo "✓ Connected"
else
    echo "✗ Disconnected"
fi

echo ""

# Resource usage
echo "Resource Usage:"
echo "---------------"
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | grep headwater
echo ""

# Logs summary
echo "Recent Log Activity (last 5 lines):"
echo "------------------------------------"
docker compose logs --tail=5 web 2>/dev/null | head -10
EOF

    chmod +x "$INSTALL_DIR/scripts/status.sh"
}

create_helper_scripts() {
    INSTALLATION_STAGE="helper_scripts"
    log_step "Creating helper scripts..."

    create_update_script
    create_uninstall_script
    create_backup_script
    create_status_script

    log_success "Helper scripts created"
}

# =============================================================================
# Systemd Service
# =============================================================================

create_systemd_service() {
    if [ "$OS" = "macos" ]; then
        return
    fi

    INSTALLATION_STAGE="systemd_service"
    log_step "Creating systemd service..."

    cat > /etc/systemd/system/headwater.service << EOF
[Unit]
Description=Headwater - Google Maps Data Extraction API
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable headwater

    log_success "Systemd service created and enabled"
}

# =============================================================================
# Start Services
# =============================================================================

start_services() {
    INSTALLATION_STAGE="service_start"
    log_step "Starting Headwater services..."

    cd "$INSTALL_DIR"

    # Pull images
    log_info "Pulling Docker images..."
    docker compose pull

    # Build if from source
    if [ "$INSTALL_SOURCE" = "github" ]; then
        log_info "Building from source..."
        docker compose build
    fi

    # Start services
    log_info "Starting containers..."
    docker compose up -d

    # Wait for services to be healthy
    log_info "Waiting for services to be ready..."
    local max_attempts=30
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        if curl -sf "http://localhost:$WEB_PORT/health" > /dev/null 2>&1; then
            log_success "Services are ready!"
            break
        fi
        attempt=$((attempt + 1))
        echo -n "."
        sleep 2
    done
    echo ""

    if [ $attempt -eq $max_attempts ]; then
        log_warn "Services may still be starting. Check status with:"
        log_info "docker compose -f $INSTALL_DIR/docker-compose.yml ps"
    fi
}

# =============================================================================
# Print Summary
# =============================================================================

print_summary() {
    CLEANUP_REQUIRED=false

    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}       Headwater Installation Complete!${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""
    echo -e "${CYAN}Installation Details:${NC}"
    echo "  Directory:     $INSTALL_DIR"
    echo "  Web Port:      $WEB_PORT"
    echo "  API Key:       $API_KEY"
    echo ""
    echo -e "${CYAN}Access URLs:${NC}"
    if [ "$SETUP_SSL" = true ]; then
        echo "  API:           https://$DOMAIN_NAME"
        echo "  Documentation: https://$DOMAIN_NAME/docs"
    else
        echo "  API:           http://localhost:$WEB_PORT"
        echo "  Documentation: http://localhost:$WEB_PORT/docs"
    fi
    echo ""
    echo -e "${CYAN}Quick Test:${NC}"
    echo "  curl -H \"X-API-Key: $API_KEY\" http://localhost:$WEB_PORT/health"
    echo ""
    echo -e "${CYAN}Helper Scripts:${NC}"
    echo "  Status:    $INSTALL_DIR/scripts/status.sh"
    echo "  Update:    $INSTALL_DIR/scripts/update.sh"
    echo "  Backup:    $INSTALL_DIR/scripts/backup.sh"
    echo "  Uninstall: $INSTALL_DIR/scripts/uninstall.sh"
    echo ""
    echo -e "${CYAN}Useful Commands:${NC}"
    echo "  View logs:     docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
    echo "  Restart:       docker compose -f $INSTALL_DIR/docker-compose.yml restart"
    echo "  Stop:          docker compose -f $INSTALL_DIR/docker-compose.yml down"
    echo ""
    echo -e "${YELLOW}Important:${NC} Save your API key and configuration!"
    echo "  Configuration file: $INSTALL_DIR/.env"
    echo ""
}

# =============================================================================
# Main Installation Flow
# =============================================================================

main() {
    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}       Headwater Installer${NC}"
    echo -e "${GREEN}       Google Maps Data Extraction API${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""

    # Pre-flight checks
    detect_os

    if [ "$OS" != "macos" ]; then
        check_privileges
    fi

    # Installation steps
    select_installation_source

    if [ "$INSTALL_SOURCE" = "github" ]; then
        gather_configuration
        install_docker
        create_directories
        clone_repository
    else
        gather_configuration
        install_docker
        create_directories
    fi

    create_env_file
    create_docker_compose
    setup_ssl
    create_helper_scripts

    if [ "$OS" != "macos" ]; then
        create_systemd_service
    fi

    start_services
    print_summary
}

# Run main function
main "$@"
