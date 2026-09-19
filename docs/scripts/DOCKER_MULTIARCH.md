# Docker Multi-Architecture Build Helper

This directory contains a helper script for building and managing multi-architecture Docker images for the Headwater project.

## Overview

The `docker_multiarch.sh` script simplifies the process of building Docker images for multiple CPU architectures (amd64/x86_64 and arm64/aarch64). This is particularly useful for ensuring your application can run on different platforms, including:

- x86_64/amd64 servers and desktops
- ARM64-based systems like Apple Silicon Macs, AWS Graviton instances, and Raspberry Pi 4

## Prerequisites

- Docker 19.03+ with BuildKit support
- Docker Buildx plugin (included in Docker Desktop 2.2.0+)
- Docker Hub account (for pushing images)

## Usage

### Basic Commands

```bash
# Show help and available commands
./scripts/docker_multiarch.sh help

# Build images for amd64 and arm64 architectures
./scripts/docker_multiarch.sh build

# Build images without using cache
./scripts/docker_multiarch.sh build-no-cache

# Push multi-architecture image to Docker Hub
./scripts/docker_multiarch.sh push yourusername

# Push multi-architecture image to Docker Hub without using cache
./scripts/docker_multiarch.sh push-no-cache yourusername

# Show current version
./scripts/docker_multiarch.sh version
```

### Via Makefile

The script is integrated with the project's Makefile for convenience:

```bash
# Build multi-architecture images
make docker-buildx

# Build multi-architecture images without cache
make docker-buildx-no-cache

# Push multi-architecture images to Docker Hub
make docker-pushx

# Push multi-architecture images to Docker Hub without cache
make docker-pushx-no-cache
```

## How It Works

### Building for Multiple Architectures

When you run `./scripts/docker_multiarch.sh build`, the script:

1. Creates a buildx builder instance if it doesn't exist
2. Builds the image for linux/amd64 platform with the `-amd64` tag suffix
3. Builds the image for linux/arm64 platform with the `-arm64` tag suffix
4. Uses the `--load` flag to make the images available in your local Docker daemon

This approach works around the limitation that Docker daemon cannot directly load multi-architecture manifest lists.

### Pushing Multi-Architecture Images

When you run `./scripts/docker_multiarch.sh push yourusername`, the script:

1. Creates a buildx builder instance if it doesn't exist
2. Builds images for both linux/amd64 and linux/arm64 platforms
3. Creates a proper multi-architecture manifest
4. Pushes everything to Docker Hub under your username
5. Tags the images with both the current version and `latest`

## Troubleshooting

### Common Issues

1. **Error: docker exporter does not currently support exporting manifest lists**
   - This error occurs when trying to use `--load` with multiple platforms
   - Solution: Our script builds each architecture separately with `--load`

2. **Error: multiple platforms feature is currently not supported for docker driver**
   - Solution: The script creates a buildx builder with the docker-container driver

3. **Error: failed to solve: rpc error: code = Unknown**
   - This can happen due to network issues or Docker Hub rate limits
   - Solution: Try again later or authenticate with Docker Hub

### Checking Image Architecture

To verify the architecture of built images:

```bash
# Check image details
docker inspect headwater:latest-amd64 | grep Architecture
docker inspect headwater:latest-arm64 | grep Architecture

# For pushed multi-arch images
docker manifest inspect yourusername/headwater:latest
```

## Advanced Usage

### Custom Tags

You can modify the script to use custom tags by editing the `build_platform` and `push_multiarch` functions.

### Additional Platforms

The script currently builds for amd64 and arm64. To add support for other platforms (e.g., arm/v7), modify the `build_multiarch` and `push_multiarch` functions.

## References

- [Docker Buildx documentation](https://docs.docker.com/buildx/working-with-buildx/)
- [Multi-platform images with Docker](https://docs.docker.com/build/building/multi-platform/)
