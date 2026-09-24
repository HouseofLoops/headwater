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

Supply chain attestations provide cryptographic verification of your Docker images. They are produced entirely by CI (`.github/workflows/release.yml`); there is no manual signing step and no signing key.

### What CI attaches to each release

1. **BuildKit SBOM and provenance attestations** (`sbom: true`, `provenance: mode=max`), which are what Docker Scout reads for this check.
2. **A keyless Cosign signature**, by digest, in both Docker Hub and GHCR (Sigstore/Fulcio, GitHub OIDC).
3. **A keyless Cosign SPDX SBOM attestation** (`cosign attest --type spdx`), by digest.

### Configure Docker Hub

- Log in to Docker Hub and open the repository
- Go to "Settings" and enable Docker Scout image analysis
- No public key needs to be registered: signing is keyless

### Verify a published image

Requires cosign >= 3 for releases after 2.2.0:

```bash
make docker-verify IMAGE=ghcr.io/rainmanjam/headwater TAG=<version>
```

For the manual `cosign` commands, the signer identity, and notes on older releases, see [SUPPLY_CHAIN_SECURITY.md](SUPPLY_CHAIN_SECURITY.md#verifying-an-image).

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
   - CI signs images keylessly with Cosign and attaches SBOM/provenance attestations
   - Enable Docker Scout on the Docker Hub repository

3. **Run as Non-Root User**:
   - We've already updated the Dockerfile to use a non-root user
   - This is a security best practice that also improves your Scout score

4. **Regular Maintenance**:
   - Check for base image updates regularly
   - Rebuild images when base images are updated (a new CI release re-signs them)
   - Monitor for vulnerabilities in your dependencies

## References

- [Docker Scout Documentation](https://docs.docker.com/scout/)
- [Cosign Documentation](https://docs.sigstore.dev/cosign/overview/)
- [SLSA Framework](https://slsa.dev/)
- [Docker Official Images](https://docs.docker.com/docker-hub/official_images/)
