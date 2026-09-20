#!/bin/bash
# Docker Multi-Architecture Build Helper Script
# This script helps with building and managing multi-architecture Docker images

set -e

# Get the current version from app/__version__.py
get_version() {
    python -c "from app.__version__ import __version__; print(__version__)"
}

# Check if buildx is available
check_buildx() {
    if ! docker buildx version > /dev/null 2>&1; then
        echo "Error: Docker Buildx is not available."
        echo "Please make sure you have Docker Desktop 2.2.0+ or Docker Engine 19.03+ with experimental features enabled."
        exit 1
    fi
}

# Create a buildx builder if it doesn't exist
create_builder() {
    if ! docker buildx inspect headwater-builder > /dev/null 2>&1; then
        echo "Creating buildx builder 'headwater-builder'..."
        docker buildx create --name headwater-builder --use
    else
        echo "Using existing buildx builder 'headwater-builder'..."
        docker buildx use headwater-builder
    fi
}

# Build for a specific platform
build_platform() {
    local platform=$1
    local version=$(get_version)
    local arch=${platform#*/}
    local no_cache=$2
    
    echo "Building for $platform..."
    if [ "$no_cache" = "true" ]; then
        echo "Using --no-cache option..."
        docker buildx build --platform $platform \
            -t headwater:$version-$arch -t headwater:latest-$arch \
            --no-cache --load .
    else
        docker buildx build --platform $platform \
            -t headwater:$version-$arch -t headwater:latest-$arch \
            --load .
    fi
}

# Build for multiple platforms
build_multiarch() {
    local no_cache=$1
    build_platform "linux/amd64" "$no_cache"
    build_platform "linux/arm64" "$no_cache"
    echo "Multi-architecture build completed successfully."
    echo "Images are tagged with -amd64 and -arm64 suffixes."
}

# Push multi-architecture image to Docker Hub
push_multiarch() {
    local docker_user=$1
    local no_cache=$2
    local version=$(get_version)
    
    if [ -z "$docker_user" ]; then
        echo "Enter your Docker Hub username:"
        read docker_user
    fi
    
    echo "Building and pushing multi-arch image for $docker_user/headwater:$version..."
    
    if [ "$no_cache" = "true" ]; then
        echo "Using --no-cache option..."
        docker buildx build --platform linux/amd64,linux/arm64 \
            -t $docker_user/headwater:$version -t $docker_user/headwater:latest \
            --no-cache --push .
    else
        docker buildx build --platform linux/amd64,linux/arm64 \
            -t $docker_user/headwater:$version -t $docker_user/headwater:latest \
            --push .
    fi
    
    echo "Successfully pushed multi-arch image to Docker Hub"
}

# List available commands
show_help() {
    echo "Docker Multi-Architecture Build Helper"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  build              Build multi-architecture images locally"
    echo "  build-no-cache     Build multi-architecture images without using cache"
    echo "  push <username>    Build and push multi-architecture images to Docker Hub"
    echo "  push-no-cache <u>  Build and push multi-architecture images without using cache"
    echo "  version            Show current version"
    echo "  help               Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 build           # Build for amd64 and arm64"
    echo "  $0 build-no-cache  # Build for amd64 and arm64 without cache"
    echo "  $0 push username   # Push to Docker Hub as username/headwater"
    echo "  $0 push-no-cache u # Push to Docker Hub without using cache"
}

# Main function
main() {
    check_buildx
    create_builder
    
    case "$1" in
        build)
            build_multiarch "false"
            ;;
        build-no-cache)
            build_multiarch "true"
            ;;
        push)
            push_multiarch "$2" "false"
            ;;
        push-no-cache)
            push_multiarch "$2" "true"
            ;;
        version)
            echo "Current version: $(get_version)"
            ;;
        help|"")
            show_help
            ;;
        *)
            echo "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
