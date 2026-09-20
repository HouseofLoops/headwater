#!/bin/bash
# Script to check for and update the base image in the Dockerfile
# This helps address the "No outdated base images" issue in Docker Hub Scout

set -e

# Default values
DOCKERFILE="Dockerfile"
BASE_IMAGE_NAME="python"
# Derived from the Dockerfile rather than hardcoded. A literal default silently
# breaks the moment the Dockerfile's Python version moves: line 79 greps for
# "FROM $BASE_IMAGE_NAME:$BASE_IMAGE_TAG", so a stale tag matches nothing and
# the workflow reports success while updating no digest at all. Override with
# --tag when you genuinely mean a different base.
BASE_IMAGE_TAG="$(
  sed -n 's|^FROM[[:space:]]\{1,\}python:\([^@[:space:]]*\).*|\1|p' Dockerfile 2>/dev/null | head -1
)"
BASE_IMAGE_TAG="${BASE_IMAGE_TAG:-3.12-slim-bookworm}"
CHECK_ONLY=false

# Function to display usage information
usage() {
  echo "Usage: $0 [options]"
  echo "Options:"
  echo "  -d, --dockerfile PATH     Path to Dockerfile (default: Dockerfile)"
  echo "  -i, --image NAME          Base image name (default: python)"
  echo "  -t, --tag TAG             Base image tag (default: 3.11-slim-bookworm)"
  echo "  -c, --check-only          Check for updates without modifying the Dockerfile"
  echo "  -h, --help                Display this help message"
  echo ""
  echo "Example:"
  echo "  $0 --dockerfile Dockerfile.prod --image python --tag 3.11-slim-bookworm"
  exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--dockerfile)
      DOCKERFILE="$2"
      shift 2
      ;;
    -i|--image)
      BASE_IMAGE_NAME="$2"
      shift 2
      ;;
    -t|--tag)
      BASE_IMAGE_TAG="$2"
      shift 2
      ;;
    -c|--check-only)
      CHECK_ONLY=true
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1"
      usage
      ;;
  esac
done

# Check if the Dockerfile exists
if [ ! -f "$DOCKERFILE" ]; then
  echo "Error: Dockerfile '$DOCKERFILE' not found"
  exit 1
fi

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
  echo "Error: Docker is not installed. Please install it first."
  exit 1
fi

# Full image reference
FULL_IMAGE="$BASE_IMAGE_NAME:$BASE_IMAGE_TAG"

echo "Checking for updates to $FULL_IMAGE..."

# Pull the latest version of the image
echo "Pulling latest version of $FULL_IMAGE..."
docker pull "$FULL_IMAGE"

# Get the current digest from the Dockerfile
# head -1 is load-bearing. This Dockerfile has two FROM lines, builder and
# production, and both are digest-pinned, so without it CURRENT_DIGEST holds
# two matches separated by a newline. That newline then lands inside the
# sed "s|...|...|" expression below and sed dies with
# "unterminated `s' command" - which is exactly how this workflow has been
# failing on its nightly schedule since digest-pinning landed.
CURRENT_DIGEST=$(grep -oP "FROM\s+$BASE_IMAGE_NAME:$BASE_IMAGE_TAG(@sha256:[a-f0-9]+)?" "$DOCKERFILE" | grep -oP '@sha256:[a-f0-9]+' | head -1 || echo "")

# Get the latest digest from Docker Hub
LATEST_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$FULL_IMAGE" | grep -oP '@sha256:[a-f0-9]+' | head -1)

echo "Current digest in Dockerfile: ${CURRENT_DIGEST:-'Not pinned to a specific digest'}"
echo "Latest digest from Docker Hub: $LATEST_DIGEST"

# Check if the digests match
if [ "$CURRENT_DIGEST" = "$LATEST_DIGEST" ]; then
  echo "✅ Base image is up-to-date"
  exit 0
fi

# If check-only flag is set, exit without modifying the Dockerfile
if [ "$CHECK_ONLY" = true ]; then
  echo "⚠️ Base image is outdated. Run without --check-only to update."
  exit 1
fi

# Update the Dockerfile
echo "Updating Dockerfile with the latest digest..."

# Create a backup of the Dockerfile
cp "$DOCKERFILE" "${DOCKERFILE}.bak"

# Update the FROM line in the Dockerfile
if [ -z "$CURRENT_DIGEST" ]; then
  # If there's no digest in the Dockerfile, add it
  sed -i.tmp "s|FROM $BASE_IMAGE_NAME:$BASE_IMAGE_TAG|FROM $BASE_IMAGE_NAME:$BASE_IMAGE_TAG$LATEST_DIGEST|" "$DOCKERFILE"
else
  # If there's already a digest, update it
  sed -i.tmp "s|FROM $BASE_IMAGE_NAME:$BASE_IMAGE_TAG$CURRENT_DIGEST|FROM $BASE_IMAGE_NAME:$BASE_IMAGE_TAG$LATEST_DIGEST|" "$DOCKERFILE"
fi

# Remove the temporary file
rm -f "${DOCKERFILE}.tmp"

echo "✅ Dockerfile updated with the latest digest"
echo "Original Dockerfile backed up to ${DOCKERFILE}.bak"
echo ""
echo "To build with the updated base image, run:"
echo "  docker build -t your-image-name ."
