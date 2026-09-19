# Supply Chain Security for Headwater

This document provides guidance on implementing supply chain security measures for the Headwater project, focusing on Docker image signing and attestations.

## Overview

Docker Hub Scout provides a health score for your Docker images based on security best practices. To improve your score, we've implemented:

1. Non-root user in the Dockerfile
2. Supply chain attestations using Cosign

## Non-Root User

The updated Dockerfile now creates and uses a non-root user (`appuser`) to run the application, which is a security best practice to limit the potential impact of container vulnerabilities.

Our implementation includes:

1. **Creating a dedicated user and group**:
   ```dockerfile
   RUN groupadd -r appuser && useradd -r -g appuser appuser
   ```

2. **Setting proper file permissions**:
   ```dockerfile
   COPY --chown=appuser:appuser . .
   RUN chmod -R 755 /app && \
       chown -R appuser:appuser /app
   ```

3. **Switching to the non-root user**:
   ```dockerfile
   USER appuser
   ```

4. **Adding a health check**:
   ```dockerfile
   HEALTHCHECK --interval=30s --timeout=3s \
     CMD curl -f http://localhost:8000/health || exit 1
   ```

5. **Setting security-enhancing environment variables**:
   ```dockerfile
   ENV PYTHONDONTWRITEBYTECODE=1 \
       PYTHONUNBUFFERED=1
   ```

These changes ensure that the application runs with the least privileges necessary, following the principle of least privilege, which is a fundamental security best practice.

## Supply Chain Attestations

Supply chain attestations provide cryptographic verification of your build process, ensuring that your Docker images haven't been tampered with and providing transparency about their contents.

### Prerequisites

To implement supply chain attestations, you'll need:

1. [Cosign](https://docs.sigstore.dev/cosign/installation/) - For signing images and creating attestations
2. [Syft](https://github.com/anchore/syft) (optional) - For generating Software Bill of Materials (SBOM)
3. [Grype](https://github.com/anchore/grype) (optional) - For vulnerability scanning

### Installation

```bash
# Install Cosign
brew install cosign  # macOS
# or
curl -O -L "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64"
sudo mv cosign-linux-amd64 /usr/local/bin/cosign
sudo chmod +x /usr/local/bin/cosign

# Install Syft (optional, for SBOM generation)
brew install syft  # macOS
# or
curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin

# Install Grype (optional, for vulnerability scanning)
brew install grype  # macOS
# or
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin
```

### Generating a Cosign Key Pair

Before you can sign images, you need to generate a key pair:

```bash
cosign generate-key-pair
```

This will create two files:
- `cosign.key` - Your private key (keep this secure!)
- `cosign.pub` - Your public key (can be shared)

For CI/CD environments, you can use:

```bash
# Generate a key pair with a password from an environment variable
export COSIGN_PASSWORD="your-secure-password"
cosign generate-key-pair
```

### Signing Images

We've provided a script (`scripts/sign_image.sh`) to simplify the signing process:

```bash
# Basic usage
./scripts/sign_image.sh --image yourusername/headwater --tag 1.0.0 --key /path/to/cosign.key

# With password from environment variable
export COSIGN_PASSWORD="your-secure-password"
./scripts/sign_image.sh --image yourusername/headwater --tag 1.0.0 --key /path/to/cosign.key

# Create SBOM attestation
./scripts/sign_image.sh --image yourusername/headwater --tag 1.0.0 --key /path/to/cosign.key --attestation sbom

# Create vulnerability attestation
./scripts/sign_image.sh --image yourusername/headwater --tag 1.0.0 --key /path/to/cosign.key --attestation vulnerability
```

### Using Makefile Commands

For convenience, we've added Makefile commands to simplify the signing process:

```bash
# Sign an image with basic provenance
make docker-sign

# Sign an image and create SBOM attestation
make docker-sign-sbom

# Sign an image and create vulnerability attestation
make docker-sign-vuln
```

These commands will prompt you for your Docker Hub username and the image tag to sign.

### Verifying Signatures

We've provided a script (`scripts/verify_attestations.sh`) to simplify the verification process:

```bash
# Basic usage
./scripts/verify_attestations.sh --image yourusername/headwater --tag 1.0.0 --key cosign.pub
```

You can also use the Makefile command:

```bash
make docker-verify
```

This will prompt you for your Docker Hub username and the image tag to verify.

For manual verification, you can use the Cosign CLI directly:

```bash
# Verify signature
cosign verify --key cosign.pub yourusername/headwater:1.0.0

# Verify provenance attestation
cosign verify-attestation --key cosign.pub yourusername/headwater:1.0.0

# Verify SBOM attestation
cosign verify-attestation --key cosign.pub --type spdx yourusername/headwater:1.0.0

# Verify vulnerability attestation
cosign verify-attestation --key cosign.pub --type vuln yourusername/headwater:1.0.0
```

## CI/CD Integration

### GitHub Actions

We've created a comprehensive GitHub Actions workflow (`.github/workflows/docker-build-sign.yml`) that handles multi-architecture builds and supply chain attestations:

```yaml
name: Build and Sign Docker Image

on:
  push:
    branches: [ main ]
    tags: [ 'v*' ]
  pull_request:
    branches: [ main ]

jobs:
  build-and-sign:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v2

      - name: Login to Docker Hub
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v2
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      # Extract version from tags or use latest
      - name: Extract version
        id: version
        run: |
          if [[ $GITHUB_REF == refs/tags/v* ]]; then
            VERSION=${GITHUB_REF#refs/tags/v}
          else
            VERSION=$(python -c "from app.__version__ import __version__; print(__version__)")
          fi
          echo "VERSION=$VERSION" >> $GITHUB_ENV
          echo "version=$VERSION" >> $GITHUB_OUTPUT

      # Build and push for PR (build only, no push)
      - name: Build for PR
        if: github.event_name == 'pull_request'
        uses: docker/build-push-action@v4
        with:
          context: .
          push: false
          tags: headwater:pr-${{ github.event.pull_request.number }}
          platforms: linux/amd64,linux/arm64
          cache-from: type=gha
          cache-to: type=gha,mode=max

      # Build and push for main branch or tags
      - name: Build and push
        if: github.event_name != 'pull_request'
        uses: docker/build-push-action@v4
        with:
          context: .
          push: true
          tags: |
            ${{ secrets.DOCKERHUB_USERNAME }}/headwater:latest
            ${{ secrets.DOCKERHUB_USERNAME }}/headwater:${{ env.VERSION }}
          platforms: linux/amd64,linux/arm64
          cache-from: type=gha
          cache-to: type=gha,mode=max

      # Sign the image (only for main branch or tags)
      - name: Install Cosign
        if: github.event_name != 'pull_request'
        uses: sigstore/cosign-installer@v2

      - name: Write Cosign key
        if: github.event_name != 'pull_request'
        run: echo "${{ secrets.COSIGN_KEY }}" > cosign.key

      - name: Sign the image
        if: github.event_name != 'pull_request'
        env:
          COSIGN_PASSWORD: ${{ secrets.COSIGN_PASSWORD }}
        run: |
          cosign sign --key cosign.key ${{ secrets.DOCKERHUB_USERNAME }}/headwater:${{ env.VERSION }}
          cosign sign --key cosign.key ${{ secrets.DOCKERHUB_USERNAME }}/headwater:latest

      - name: Create provenance attestation
        if: github.event_name != 'pull_request'
        env:
          COSIGN_PASSWORD: ${{ secrets.COSIGN_PASSWORD }}
        run: |
          cosign attest --predicate <(cosign generate-attestation ${{ secrets.DOCKERHUB_USERNAME }}/headwater:${{ env.VERSION }}) \
            --key cosign.key ${{ secrets.DOCKERHUB_USERNAME }}/headwater:${{ env.VERSION }}
          
          cosign attest --predicate <(cosign generate-attestation ${{ secrets.DOCKERHUB_USERNAME }}/headwater:latest) \
            --key cosign.key ${{ secrets.DOCKERHUB_USERNAME }}/headwater:latest

      # Generate and attest SBOM
      - name: Install Syft
        if: github.event_name != 'pull_request'
        uses: anchore/sbom-action/download-syft@v0.14.3

      - name: Generate SBOM
        if: github.event_name != 'pull_request'
        run: |
          syft ${{ secrets.DOCKERHUB_USERNAME }}/headwater:${{ env.VERSION }} -o spdx-json > sbom.json

      - name: Attest SBOM
        if: github.event_name != 'pull_request'
        env:
          COSIGN_PASSWORD: ${{ secrets.COSIGN_PASSWORD }}
        run: |
          cosign attest --predicate sbom.json --key cosign.key --type spdx \
            ${{ secrets.DOCKERHUB_USERNAME }}/headwater:${{ env.VERSION }}
          
          cosign attest --predicate sbom.json --key cosign.key --type spdx \
            ${{ secrets.DOCKERHUB_USERNAME }}/headwater:latest
```

This workflow:

1. Builds for both amd64 and arm64 architectures
2. Handles pull requests (build only, no push)
3. Extracts version information from tags or the app version
4. Signs images with Cosign
5. Creates provenance attestations
6. Generates and attests SBOM

### GitLab CI

For GitLab CI, add this to your `.gitlab-ci.yml`:

```yaml
build_sign_push:
  stage: build
  image: docker:latest
  services:
    - docker:dind
  before_script:
    - apk add --no-cache curl
    - curl -O -L "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64"
    - mv cosign-linux-amd64 /usr/local/bin/cosign
    - chmod +x /usr/local/bin/cosign
    - echo "$COSIGN_KEY" > cosign.key
    - docker login -u $DOCKERHUB_USERNAME -p $DOCKERHUB_TOKEN
  script:
    - docker build -t $DOCKERHUB_USERNAME/headwater:latest .
    - docker push $DOCKERHUB_USERNAME/headwater:latest
    - cosign sign --key cosign.key $DOCKERHUB_USERNAME/headwater:latest
    - cosign attest --predicate <(cosign generate-attestation $DOCKERHUB_USERNAME/headwater:latest) --key cosign.key $DOCKERHUB_USERNAME/headwater:latest
  only:
    - main
    - tags
```

## Docker Hub Configuration

After signing your images, you need to configure Docker Hub to recognize and display the attestations:

1. Log in to Docker Hub
2. Navigate to your repository
3. Go to the "Settings" tab
4. Under "Security & Vulnerability Scanning", enable "Use Docker Scout"
5. Add your public key (`cosign.pub`) to the "Trusted Publishers" section

## Best Practices

1. **Key Security**: Store your private key securely and never commit it to your repository
2. **CI/CD Secrets**: Use your CI/CD platform's secrets management to store the key and password
3. **Regular Updates**: Regularly update your base images and dependencies to address vulnerabilities
4. **Multiple Attestations**: Consider creating multiple types of attestations (provenance, SBOM, vulnerability)
5. **Verification**: Implement verification in your deployment process to ensure only signed images are deployed

## References

- [Cosign Documentation](https://docs.sigstore.dev/cosign/overview/)
- [Docker Scout](https://docs.docker.com/scout/)
- [SLSA Framework](https://slsa.dev/)
- [Syft Documentation](https://github.com/anchore/syft)
- [Grype Documentation](https://github.com/anchore/grype)
