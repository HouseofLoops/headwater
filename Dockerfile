# Dockerfile - Multi-stage build for Headwater API
# Optimized for size, security, and caching
# Includes Playwright for Google Maps scraping

# =============================================================================
# Stage 1: Builder - Install dependencies and build assets
# =============================================================================
# Base image is digest-pinned so the weekly update-base-image workflow has a
# concrete reference to diff against; a bare tag silently floats and gives the
# workflow nothing to update. Moved 3.12 -> 3.14 to match the interpreter the
# test suite and requirements.lock are resolved against, and for the longer
# upstream security window. The move needed hiredis off 3.2.1, which ships no
# cp314 wheel; the regenerated lock takes 3.4.1, which does. Every other pin is
# either pure-Python or already publishes a cp314 build.
#
# ACTION REQUIRED (owner of scripts/): scripts/update_base_image.sh:10 still
# defaults BASE_IMAGE_TAG to "3.11-slim-bookworm" and .github/workflows/
# update-base-image.yml invokes it with no --tag, so its grep will not match
# the FROM lines below until that default becomes "3.14-slim-bookworm".
# python:3.14-slim-bookworm as of 2026-09-19
FROM python:3.14-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS builder

WORKDIR /build

# Install build dependencies
# apt-get upgrade, not just install. The base image ships packages that are
# never touched again otherwise: a Trivy scan found libpcre2-8-0 pinned at
# 10.42-1 with 10.42-1+deb12u1 available, carrying three HIGH CVEs that a
# plain `install` leaves in place. The digest pin above still makes the build
# reproducible; this only applies security updates published against it.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for isolation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies (separate layer for caching)
# Install from the fully-resolved, hash-pinned lock file rather than the
# loose spec so the image's transitive closure is reproducible and auditable.
# Regenerate with:
#   uv pip compile requirements.txt --python-version 3.14 --universal \
#       --generate-hashes --no-header -o requirements.lock
COPY requirements.lock .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --require-hashes -r requirements.lock

# NLTK corpora are no longer baked in: nltk is not installed. See the note in
# app/api/google_news/google_news_api.py - it carries an unfixed advisory
# (PYSEC-2026-3740) and is only needed for article summary and keywords. This
# also removes a network call from the image build.
# Restore this RUN, the NLTK_DATA env and the two /opt/nltk_data lines below
# when a fixed nltk is re-pinned.


# =============================================================================
# Stage 2: Production - Minimal runtime image with Playwright
# =============================================================================
# python:3.14-slim-bookworm as of 2026-09-19 (keep in sync with the builder stage)
FROM python:3.14-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS production

# OCI image metadata. The image carried none before, so `docker inspect` on a
# pulled image said nothing about what it was, where it came from or how it is
# licensed, and registry UIs had nothing to show. These are the standard
# org.opencontainers.image.* keys, which Docker Hub, GHCR, Trivy and Syft all
# read.
#
# revision and created are build arguments rather than hardcoded values: baking
# a commit into a tracked file means it is wrong the moment anything else is
# committed. CI passes the real ones; a local build leaves them empty rather
# than claiming a provenance it does not have.
ARG VCS_REF=""
ARG BUILD_DATE=""
LABEL org.opencontainers.image.title="Headwater" \
      org.opencontainers.image.description="One self-hosted API for Google Maps, News, Trends and Autocomplete, plus YouTube transcripts. Normalised JSON, no per-call vendor pricing." \
      org.opencontainers.image.source="https://github.com/rainmanjam/headwater" \
      org.opencontainers.image.url="https://github.com/rainmanjam/headwater" \
      org.opencontainers.image.documentation="https://github.com/rainmanjam/headwater/blob/main/README.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="rainmanjam" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.base.name="python:3.14-slim-bookworm" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.created="$BUILD_DATE"

# Security: Set environment variables early
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

WORKDIR /app

# Install runtime dependencies including Playwright browser deps
# Note: We need more packages for Playwright/Chromium
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    # Playwright/Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libgtk-3-0 \
    # Fonts for proper rendering
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Playwright browsers (Chromium only for smaller size)
# Do this as root before switching to appuser
RUN mkdir -p /opt/playwright-browsers && \
    playwright install chromium && \
    chmod -R 755 /opt/playwright-browsers

# Create required directories with proper permissions
RUN mkdir -p /app/.tldextract_cache && \
    chown -R appuser:appuser /app /opt/playwright-browsers

# Copy application code
COPY --chown=appuser:appuser . .

# Set proper permissions
RUN chmod -R 755 /app

# Switch to non-root user
USER appuser

# Expose port (documentation only)
EXPOSE 8000

# Health check with proper intervals and retries
# - Start checking after 10s (start_period)
# - Check every 30s
# - Timeout after 10s
# - Mark unhealthy after 3 consecutive failures
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with optimized settings
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
