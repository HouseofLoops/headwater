# Docker Hub Scout Health Score Guide

This document provides guidance on addressing the three main issues that affect your Docker Hub Scout health score:

1. No unapproved base images
2. Missing supply chain attestation(s)
3. No outdated base images

## 1. No Unapproved Base Images

Docker Hub Scout considers official images from Docker Hub as approved base images. The Dockerfile uses an official Python image, pinned by tag and digest (the weekly `update-base-image` workflow refreshes the digest):

```dockerfile
# Official Python image from Docker Hub (approved base image), digest-pinned
FROM python:3.14-slim-trixie@sha256:<digest> AS builder
```

### Best Practices for Base Images

1. **Use Official Images**: Always use official images from Docker Hub when possible
2. **Pin to Specific Versions**: Use specific version tags rather than floating tags like `latest`
3. **Use Minimal Images**: Prefer slim or alpine variants to reduce attack surface
4. **Consider Distroless Images**: For production, consider using distroless images

## 2. Missing Supply Chain Attestation(s)

Supply chain attestations provide cryptographic verification of your Docker images. We've implemented a comprehensive solution:

### Steps to Implement Supply Chain Attestations

1. **Generate a Cosign Key Pair**:
   ```bash
   cosign generate-key-pair
   ```

2. **Sign Your Docker Images**:
   ```bash
   # Basic signing
   make docker-sign
   
   # With SBOM attestation (recommended)
   make docker-sign-sbom
   
   # With vulnerability attestation
   make docker-sign-vuln
   ```

3. **Configure Docker Hub**:
   - Log in to Docker Hub
   - Navigate to your repository
   - Go to "Settings" > "Security & Vulnerability Scanning"
   - Enable "Use Docker Scout"
   - Under "Trusted Publishers", add your public key (`cosign.pub`)

4. **Verify Your Attestations**:
   ```bash
   make docker-verify
   ```

For more detailed information, see [scripts/SUPPLY_CHAIN_SECURITY.md](SUPPLY_CHAIN_SECURITY.md).

## 3. No Outdated Base Images

Keeping base images up-to-date is crucial for security. We've created tools to help with this:

### Using the Base Image Update Tool

1. **Check if Base Image is Up-to-Date**:
   ```bash
   make check-base-image
   ```

2. **Update Base Image to Latest Digest**:
   ```bash
   make update-base-image
   ```

3. **Rebuild Your Docker Image**:
   ```bash
   make docker-build
   ```

### Automating Base Image Updates

For CI/CD environments, you can add a scheduled job to check for base image updates:

```yaml
# GitHub Actions example
name: Update Base Image

on:
  schedule:
    - cron: '0 0 * * 1'  # Weekly on Monday at midnight

jobs:
  update-base-image:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3
        
      - name: Update base image
        run: |
          ./scripts/update_base_image.sh
          
      - name: Create Pull Request
        uses: peter-evans/create-pull-request@v5
        with:
          title: 'chore: update base image to latest digest'
          commit-message: 'chore: update base image to latest digest'
          branch: update-base-image
          delete-branch: true
```

## Putting It All Together

To achieve a high Docker Hub Scout health score:

1. **Use Approved Base Images**:
   - Use official images from Docker Hub
   - Pin to specific versions
   - Keep base images up-to-date

2. **Implement Supply Chain Attestations**:
   - Sign your images with Cosign
   - Create SBOM attestations
   - Configure Docker Hub to recognize your attestations

3. **Run as Non-Root User**:
   - We've already updated the Dockerfile to use a non-root user
   - This is a security best practice that also improves your Scout score

4. **Regular Maintenance**:
   - Check for base image updates regularly
   - Rebuild and re-sign images when base images are updated
   - Monitor for vulnerabilities in your dependencies

## References

- [Docker Scout Documentation](https://docs.docker.com/scout/)
- [Cosign Documentation](https://docs.sigstore.dev/cosign/overview/)
- [SLSA Framework](https://slsa.dev/)
- [Docker Official Images](https://docs.docker.com/docker-hub/official_images/)
