# Supply Chain Security for Headwater

This document describes the supply chain security measures for the Headwater project, focusing on how published Docker images are signed and attested, and how to verify them.

## Overview

Docker Hub Scout provides a health score for your Docker images based on security best practices. To improve your score, we've implemented:

1. Non-root user in the Dockerfile
2. Keyless Cosign signatures and SBOM attestations, produced by CI

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

Supply chain attestations provide cryptographic verification of the build process, so you can check that an image came from this repository's CI and was not tampered with, and see what it contains.

### How images are signed

Images are signed **only by CI**, in [`.github/workflows/release.yml`](../../.github/workflows/release.yml), using Cosign **keyless** signing (Sigstore/Fulcio with the workflow's GitHub OIDC token). There is no long-lived signing key and no public key to distribute: a verifier instead checks that the signing certificate was issued to this exact workflow.

For each release, CI:

1. Builds the multi-arch image (amd64, arm64) and pushes it to `rainmanjam/headwater` (Docker Hub) and `ghcr.io/rainmanjam/headwater`, with BuildKit SBOM and provenance attestations attached.
2. Signs the image **by digest** in both registries with `cosign sign`.
3. Generates an SPDX SBOM with Syft and attaches it with `cosign attest --type spdx`, again by digest.

The signer identity to verify against is:

| Field | Value |
|---|---|
| Certificate identity | `https://github.com/rainmanjam/headwater/.github/workflows/release.yml@refs/heads/main` |
| OIDC issuer | `https://token.actions.githubusercontent.com` |

There is no local or key-based signing path. Images you build yourself are not signed.

### Which releases are signed

| Release | Signature | SBOM attestation | Cosign needed to verify |
|---|---|---|---|
| 1.x, 2.0.0 | none | none | n/a |
| 2.1.0, 2.2.0 | keyless (legacy format) | keyless (legacy `.att` format) | signature: 2.x or 3.x; SBOM attestation: **2.x only** |
| After 2.2.0 | keyless (cosign v3 bundle) | keyless (cosign v3 bundle) | **3.x or newer** |

From the release after 2.2.0, CI signs with cosign v3, which stores signatures in the new Sigstore bundle format (as OCI referrers, or a `sha256-<digest>` fallback tag). Cosign 2.x cannot find those signatures. Cosign 3 can verify the 2.1.0/2.2.0 signatures, but cannot match their legacy-format SBOM attestation by `--type`, so use cosign 2.x for that one check.

### Installing Cosign

```bash
brew install cosign  # macOS
# or see https://docs.sigstore.dev/cosign/system_config/installation/
cosign version       # must report v3.x or newer for current releases
```

### Verifying an image

The quickest way is the Makefile target, which checks the signature and the SPDX SBOM attestation non-interactively and fails if cosign is missing or older than 3:

```bash
make docker-verify IMAGE=ghcr.io/rainmanjam/headwater TAG=<version>
make docker-verify IMAGE=rainmanjam/headwater DIGEST=sha256:<digest>
```

Or run Cosign directly. Pinning by digest is best practice, because a tag can be moved:

```bash
# Signature
cosign verify \
  --certificate-identity https://github.com/rainmanjam/headwater/.github/workflows/release.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/rainmanjam/headwater@sha256:<digest>

# SPDX SBOM attestation
cosign verify-attestation --type spdx \
  --certificate-identity https://github.com/rainmanjam/headwater/.github/workflows/release.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/rainmanjam/headwater@sha256:<digest>
```

To find the digest for a tag: `docker buildx imagetools inspect ghcr.io/rainmanjam/headwater:<version>`. The release notes for each version also list the digest.

## Docker Hub Configuration

Docker Scout reads the BuildKit SBOM and provenance attestations that CI attaches at build time. To see them, enable Docker Scout on the repository (Docker Hub > repository > Settings > enable "Docker Scout image analysis"). No key needs to be registered, because signing is keyless.

## Best Practices

1. **No signing keys**: Signing is keyless in CI. Never add a Cosign private key to the repo or the build context; `.gitignore` and `.dockerignore` still block `cosign.key` and `*.key` as a safety net.
2. **Verify by identity**: Always pass the exact `--certificate-identity` and `--certificate-oidc-issuer`. Verifying without them only proves that *someone* signed the image.
3. **Pin by digest**: Deploy and verify `image@sha256:...` rather than a tag.
4. **Regular Updates**: Regularly update your base images and dependencies to address vulnerabilities.
5. **Verification in deployment**: Verify signatures in your deployment process so only CI-signed images run.

## References

- [Cosign Documentation](https://docs.sigstore.dev/cosign/overview/)
- [Keyless signing](https://docs.sigstore.dev/cosign/signing/overview/)
- [Docker Scout](https://docs.docker.com/scout/)
- [SLSA Framework](https://slsa.dev/)
- [Syft Documentation](https://github.com/anchore/syft)
- [Grype Documentation](https://github.com/anchore/grype)
