#!/bin/bash
# Script for signing Docker images with Cosign for supply chain attestations
# This script should be run in your CI/CD pipeline after building the image

set -e

# Default values
IMAGE_NAME=""
IMAGE_TAG=""
COSIGN_KEY=""
COSIGN_PASSWORD=""
ATTESTATION_TYPE="provenance"

# Function to display usage information
usage() {
  echo "Usage: $0 [options]"
  echo "Options:"
  echo "  -i, --image IMAGE_NAME    Docker image name (required)"
  echo "  -t, --tag IMAGE_TAG       Docker image tag (default: latest)"
  echo "  -k, --key COSIGN_KEY      Path to Cosign private key (required)"
  echo "  -p, --password PASSWORD   Cosign key password (can be set via COSIGN_PASSWORD env var)"
  echo "  -a, --attestation TYPE    Attestation type (default: provenance, options: provenance, sbom, vulnerability)"
  echo "  -h, --help                Display this help message"
  echo ""
  echo "Example:"
  echo "  $0 --image myusername/headwater --tag 1.0.0 --key /path/to/cosign.key"
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
      COSIGN_KEY="$2"
      shift 2
      ;;
    -p|--password)
      COSIGN_PASSWORD="$2"
      shift 2
      ;;
    -a|--attestation)
      ATTESTATION_TYPE="$2"
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

if [ -z "$COSIGN_KEY" ]; then
  echo "Error: Cosign key path is required"
  usage
fi

# Set default tag if not provided
if [ -z "$IMAGE_TAG" ]; then
  IMAGE_TAG="latest"
fi

# Full image reference
FULL_IMAGE="$IMAGE_NAME:$IMAGE_TAG"

# Check if cosign is installed
if ! command -v cosign &> /dev/null; then
  echo "Error: cosign is not installed. Please install it first."
  echo "Installation instructions: https://docs.sigstore.dev/cosign/installation/"
  exit 1
fi

# Export password if provided
if [ -n "$COSIGN_PASSWORD" ]; then
  export COSIGN_PASSWORD
fi

echo "Signing image: $FULL_IMAGE"

# Sign the image
cosign sign --key "$COSIGN_KEY" "$FULL_IMAGE"

# Create attestations based on type
case "$ATTESTATION_TYPE" in
  provenance)
    echo "Creating provenance attestation..."
    cosign attest --predicate <(cosign generate-attestation "$FULL_IMAGE") --key "$COSIGN_KEY" "$FULL_IMAGE"
    ;;
  sbom)
    echo "Creating SBOM attestation..."
    # Generate SBOM using syft
    if ! command -v syft &> /dev/null; then
      echo "Warning: syft is not installed. Skipping SBOM generation."
    else
      syft "$FULL_IMAGE" -o spdx-json > sbom.json
      cosign attest --predicate sbom.json --key "$COSIGN_KEY" --type spdx "$FULL_IMAGE"
      rm sbom.json
    fi
    ;;
  vulnerability)
    echo "Creating vulnerability attestation..."
    # Generate vulnerability report using grype
    if ! command -v grype &> /dev/null; then
      echo "Warning: grype is not installed. Skipping vulnerability scan."
    else
      grype "$FULL_IMAGE" -o json > vulnerabilities.json
      cosign attest --predicate vulnerabilities.json --key "$COSIGN_KEY" --type vuln "$FULL_IMAGE"
      rm vulnerabilities.json
    fi
    ;;
  *)
    echo "Unknown attestation type: $ATTESTATION_TYPE"
    exit 1
    ;;
esac

echo "Image signed and attested successfully: $FULL_IMAGE"
