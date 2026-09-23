.PHONY: help install run test lint docker-build docker-run docker-compose-up docker-compose-down docker-compose-dev dev prod clean-start update test-and-build ci logs restart rebuild check-env debug-docker docker-push version version-patch version-minor version-major docker-buildx docker-buildx-no-cache docker-pushx docker-pushx-no-cache docker-sign docker-sign-sbom docker-sign-vuln docker-verify update-base-image check-base-image test-proxy test-apis clear-cache health-check

help:
	@echo "Available commands:"
	@echo "  make install            - Install dependencies"
	@echo "  make run                - Run development server"
	@echo "  make test               - Run tests"
	@echo "  make lint               - Run ruff (blocking rules, then advisory stats)"
	@echo "  make docker-build       - Build Docker image"
	@echo "  make docker-nocache-build - Build Docker image without cache"
	@echo "  make docker-run         - Run Docker container"
	@echo "  make docker-compose-up  - Start with Docker Compose"
	@echo "  make docker-compose-down- Stop Docker Compose containers"
	@echo ""
	@echo "Headwater specific commands:"
	@echo "  make test-proxy         - Test proxy configuration"
	@echo "  make test-apis          - Test external API integrations"
	@echo "  make clear-cache        - Clear Redis cache"
	@echo "  make health-check       - Check API health endpoints"
	@echo ""
	@echo "Combined commands:"
	@echo "  make dev                - Run development server with auto-reload"
	@echo "  make prod               - Build and run production container"
	@echo "  make clean-start        - Remove containers, rebuild and start"
	@echo "  make update             - Update dependencies and rebuild"
	@echo "  make test-and-build     - Run tests and build if they pass"
	@echo "  make ci                 - Run full CI pipeline (test, build, run)"
	@echo "  make logs               - Show logs from running containers"
	@echo "  make restart            - Restart running containers"
	@echo "  make rebuild            - Rebuild and restart containers"
	@echo "  make check-env          - Check environment issues"
	@echo "  make debug-docker       - Debug environment issues in Docker"
	@echo ""
	@echo "Version commands:"
	@echo "  make version            - Show current version"
	@echo "  make version-patch      - Increment patch version (1.0.0 -> 1.0.1)"
	@echo "  make version-minor      - Increment minor version (1.0.0 -> 1.1.0)"
	@echo "  make version-major      - Increment major version (1.0.0 -> 2.0.0)"
	@echo "  make docker-push        - Build and push Docker image to Docker Hub"
	@echo "  make docker-buildx      - Build multi-arch Docker image (amd64, arm64)"
	@echo "  make docker-buildx-no-cache - Build multi-arch Docker image without cache"
	@echo "  make docker-pushx       - Build and push multi-arch Docker image to Docker Hub"
	@echo "  make docker-pushx-no-cache - Build and push multi-arch Docker image without cache"
	@echo ""
	@echo "Docker image signing commands:"
	@echo "  make docker-sign        - Sign Docker image with Cosign"
	@echo "  make docker-sign-sbom   - Sign Docker image and create SBOM attestation"
	@echo "  make docker-sign-vuln   - Sign Docker image and create vulnerability attestation"
	@echo "  make docker-verify      - Verify Docker image signatures and attestations"
	@echo ""
	@echo "Base image management:"
	@echo "  make update-base-image  - Update base image to latest digest"
	@echo "  make check-base-image   - Check if base image is up-to-date"
	@echo ""
	@echo "Multi-architecture Docker commands:"
	@echo "  ./scripts/docker_multiarch.sh build       - Build for amd64 and arm64 platforms"
	@echo "  ./scripts/docker_multiarch.sh push USER   - Push multi-arch image to Docker Hub"
	@echo "  ./scripts/docker_multiarch.sh version     - Show current version"
	@echo "  ./scripts/docker_multiarch.sh help        - Show helper script usage"

install:
	pip install -r requirements-dev.txt

install-prod:
	pip install -r requirements.txt

run:
	uvicorn main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest --cov=app tests/

# Mirrors the CI lint job (.github/workflows/_verify.yml): the first command is
# the blocking gate, the second prints the advisory full-rule-set counts.
lint:
	ruff check --select E9,F63,F7,F82 .
	ruff check --exit-zero --statistics .

docker-build:
	docker build -t headwater .

docker-nocache-build:
	docker build --no-cache -t headwater .

docker-buildx:
	@echo "Building multi-arch Docker image (linux/amd64,linux/arm64)..."
	@./scripts/docker_multiarch.sh build

docker-buildx-no-cache:
	@echo "Building multi-arch Docker image without cache (linux/amd64,linux/arm64)..."
	@./scripts/docker_multiarch.sh build-no-cache

docker-run:
	docker run -p 8000:8000 --env-file .env headwater

docker-compose-up:
	docker-compose up -d

docker-compose-down:
	docker-compose down

# Headwater specific commands
test-proxy:
	@echo "Testing proxy configuration..."
	@if [ -z "$$PROXY_URL" ]; then \
		echo "Error: PROXY_URL environment variable is not set"; \
		exit 1; \
	fi
	@echo "Proxy URL: $$PROXY_URL"
	@curl -s -i --proxy "$$PROXY_URL" "https://geo.brdtest.com/welcome.txt?product=dc&method=native" || echo "Proxy test failed"

test-apis:
	@echo "Testing API integrations..."
	@python -c "import requests; print('Google News API: ' + ('OK' if requests.get('https://news.google.com/').status_code == 200 else 'FAIL'))"
	@python -c "import requests; print('Google Trends API: ' + ('OK' if requests.get('https://trends.google.com/').status_code == 200 else 'FAIL'))"
	@python -c "import requests; print('YouTube API: ' + ('OK' if requests.get('https://www.youtube.com/').status_code == 200 else 'FAIL'))"

clear-cache:
	@echo "Clearing Redis cache..."
	@if [ -z "$$REDIS_URL" ]; then \
		echo "Error: REDIS_URL environment variable is not set"; \
		exit 1; \
	fi
	@python -c "import redis; r = redis.from_url('$$REDIS_URL'); r.flushall(); print('Cache cleared successfully')"

health-check:
	@echo "Checking API health..."
	@curl -s http://localhost:8000/health || echo "Health check failed"

# Combined commands
dev:
	uvicorn main:app --reload --host 0.0.0.0 --port 8000

prod:
	docker-compose build
	docker-compose up -d

clean-start:
	docker-compose down -v
	docker-compose rm -f
	docker-compose build --no-cache
	docker-compose up -d

update:
	pip install -U -r requirements-dev.txt
	docker-compose build --no-cache
	docker-compose up -d

test-and-build:
	pytest --cov=app tests/ && docker-compose build

ci:
	pytest --cov=app tests/
	docker-compose build
	docker-compose up -d

logs:
	docker-compose logs -f

restart:
	docker-compose restart

rebuild:
	docker-compose down
	docker-compose build
	docker-compose up -d

check-env:
	python scripts/check_env.py

debug-docker:
	docker-compose run --rm headwater python /app/scripts/check_env.py

docker-push:
	@echo "Building and pushing Docker image to Docker Hub..."
	@python -c "from app.__version__ import __version__; print(f'Current version: {__version__}')"
	@VERSION=$$(python -c "from app.__version__ import __version__; print(__version__)") && \
	echo "Building version $$VERSION" && \
	docker build -t headwater:$$VERSION -t headwater:latest . && \
	echo "Enter your Docker Hub username:" && \
	read DOCKER_USER && \
	docker tag headwater:$$VERSION $$DOCKER_USER/headwater:$$VERSION && \
	docker tag headwater:latest $$DOCKER_USER/headwater:latest && \
	docker push $$DOCKER_USER/headwater:$$VERSION && \
	docker push $$DOCKER_USER/headwater:latest && \
	echo "Successfully pushed version $$VERSION to Docker Hub"

docker-pushx:
	@echo "Building and pushing multi-arch Docker image to Docker Hub..."
	@echo "Enter your Docker Hub username:"
	@read DOCKER_USER && ./scripts/docker_multiarch.sh push $$DOCKER_USER

docker-pushx-no-cache:
	@echo "Building and pushing multi-arch Docker image to Docker Hub without cache..."
	@echo "Enter your Docker Hub username:"
	@read DOCKER_USER && ./scripts/docker_multiarch.sh push-no-cache $$DOCKER_USER

# Docker image signing
# The private signing key lives OUTSIDE the repo so it can never enter the
# Docker build context (Dockerfile ends in `COPY . .`). Override with:
#   make docker-sign COSIGN_KEY=/path/to/cosign.key
COSIGN_KEY ?= $(HOME)/.secrets/headwater/cosign.key
COSIGN_PUB ?= cosign.pub

docker-sign:
	@echo "Signing Docker image with Cosign..."
	@echo "Enter your Docker Hub username:"
	@read DOCKER_USER && \
	echo "Enter image tag (default: latest):" && \
	read IMAGE_TAG && \
	IMAGE_TAG=$${IMAGE_TAG:-latest} && \
	./scripts/sign_image.sh --image $$DOCKER_USER/headwater --tag $$IMAGE_TAG --key $(COSIGN_KEY)

docker-sign-sbom:
	@echo "Signing Docker image and creating SBOM attestation..."
	@echo "Enter your Docker Hub username:"
	@read DOCKER_USER && \
	echo "Enter image tag (default: latest):" && \
	read IMAGE_TAG && \
	IMAGE_TAG=$${IMAGE_TAG:-latest} && \
	./scripts/sign_image.sh --image $$DOCKER_USER/headwater --tag $$IMAGE_TAG --key $(COSIGN_KEY) --attestation sbom

docker-sign-vuln:
	@echo "Signing Docker image and creating vulnerability attestation..."
	@echo "Enter your Docker Hub username:"
	@read DOCKER_USER && \
	echo "Enter image tag (default: latest):" && \
	read IMAGE_TAG && \
	IMAGE_TAG=$${IMAGE_TAG:-latest} && \
	./scripts/sign_image.sh --image $$DOCKER_USER/headwater --tag $$IMAGE_TAG --key $(COSIGN_KEY) --attestation vulnerability

docker-verify:
	@echo "Verifying Docker image signatures and attestations..."
	@echo "Enter your Docker Hub username:"
	@read DOCKER_USER && \
	echo "Enter image tag (default: latest):" && \
	read IMAGE_TAG && \
	IMAGE_TAG=$${IMAGE_TAG:-latest} && \
	./scripts/verify_attestations.sh --image $$DOCKER_USER/headwater --tag $$IMAGE_TAG --key $(COSIGN_PUB)

# Base image management
update-base-image:
	@echo "Checking for base image updates..."
	@./scripts/update_base_image.sh

check-base-image:
	@echo "Checking if base image is up-to-date..."
	@./scripts/update_base_image.sh --check-only

version:
	@python -c "from app.__version__ import __version__; print(f'Current version: {__version__}')"

version-patch:
	@python scripts/increment_version.py patch
	@$(MAKE) version

version-minor:
	@python scripts/increment_version.py minor
	@$(MAKE) version

version-major:
	@python scripts/increment_version.py major
	@$(MAKE) version
