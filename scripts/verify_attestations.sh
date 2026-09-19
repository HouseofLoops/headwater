#!/bin/bash
# Script to verify Docker image signatures and attestations
# This script helps verify that your Docker images have been properly signed and attested

set -e

# Default values
IMAGE_NAME=""
IMAGE_TAG="latest"
PUBLIC_KEY="cosign.pub"

# Function to display usage information
usage() {
  echo "Usage: $0 [options]"
  echo "Options:"
  echo "  -i, --image IMAGE_NAME    Docker image name (required)"
  echo "  -t, --tag IMAGE_TAG       Docker image tag (default: latest)"
  echo "  -k, --key PUBLIC_KEY      Path to Cosign public key (default: cosign.pub)"
  echo "  -h, --help                Display this help message"
  echo ""
  echo "Example:"
  echo "  $0 --image yourusername/headwater --tag 1.0.0 --key /path/to/cosign.pub"
  exit 1
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--image)
      IMAGE_NAME="$2"
      shift 2
      ;;
    -t|--tag)
      IMAGE_TAG="$2"
      shift 2
      ;;
    -k|--key)
      PUBLIC_KEY="$2"
      shift 2
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

# Validate required parameters
if [ -z "$IMAGE_NAME" ]; then
  echo "Error: Image name is required"
  usage
fi

# Check if cosign is installed
if ! command -v cosign &> /dev/null; then
  echo "Error: cosign is not installed. Please install it first."
  echo "Installation instructions: https://docs.sigstore.dev/cosign/installation/"
  exit 1
fi

# Full image reference
FULL_IMAGE="$IMAGE_NAME:$IMAGE_TAG"

echo "Verifying signatures and attestations for $FULL_IMAGE"
echo "Using public key: $PUBLIC_KEY"
echo ""

# Check if the public key exists
if [ ! -f "$PUBLIC_KEY" ]; then
  echo "Error: Public key file '$PUBLIC_KEY' not found"
  exit 1
fi

# Verify signature
echo "=== Verifying Image Signature ==="
if cosign verify --key "$PUBLIC_KEY" "$FULL_IMAGE"; then
  echo "✅ Image signature verification successful"
else
  echo "❌ Image signature verification failed"
fi
echo ""

# Verify provenance attestation
echo "=== Verifying Provenance Attestation ==="
if cosign verify-attestation --key "$PUBLIC_KEY" "$FULL_IMAGE" &> /dev/null; then
  echo "✅ Provenance attestation verification successful"
else
  echo "❌ Provenance attestation verification failed or not found"
fi
echo ""

# Verify SBOM attestation
echo "=== Verifying SBOM Attestation ==="
if cosign verify-attestation --key "$PUBLIC_KEY" --type spdx "$FULL_IMAGE" &> /dev/null; then
  echo "✅ SBOM attestation verification successful"
else
  echo "❌ SBOM attestation verification failed or not found"
fi
echo ""

# Verify vulnerability attestation
echo "=== Verifying Vulnerability Attestation ==="
if cosign verify-attestation --key "$PUBLIC_KEY" --type vuln "$FULL_IMAGE" &> /dev/null; then
  echo "✅ Vulnerability attestation verification successful"
else
  echo "❌ Vulnerability attestation verification failed or not found"
fi
echo ""

echo "Verification complete for $FULL_IMAGE"
echo "If any verifications failed, you may need to sign or attest the image using the appropriate commands."
echo "See scripts/SUPPLY_CHAIN_SECURITY.md for more information."
