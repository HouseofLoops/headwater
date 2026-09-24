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
3. Generates an SPDX JSON SBOM with Syft and attaches it with `cosign attest --type spdxjson`, again by digest.
4. Verifies both signatures and both attestations with the same identity a user would check, before the GitHub release is published.

The signer identity to verify against is:

| Field | Value |
|---|---|
| Certificate identity | `https://github.com/rainmanjam/headwater/.github/workflows/release.yml@refs/heads/main` |
| OIDC issuer | `https://token.actions.githubusercontent.com` |

There is no local or key-based signing path. Images you build yourself are not signed.

### Which releases are signed

Which cosign to use (tested against real images, keyless, on both registries):

| Release | Signature | SBOM attestation |
|---|---|---|
| After 2.2.0 (signed with cosign 3) | cosign 2.6+ or 3.x | cosign 2.6+ or 3.x, `--type spdxjson` |
| 2.1.0, 2.2.0 (signed with cosign 2) | cosign 2.6+ or 3.x | **cosign 2.x only**, `--type spdx` or `spdxjson` |
| 2.0.0 and 1.x | not signed | none |

The 2.1.0/2.2.0 exception: their SBOM was attested with `--type spdx`, which
made cosign embed the SPDX JSON as a single string. cosign 3 requires the
predicate to be a JSON object and rejects it. From the release after 2.2.0 the
SBOM is attested with `--type spdxjson`, as a real JSON object.

CI signs with cosign v3, which writes the Sigstore bundle format and stores it
as OCI referrers (or a `sha256-<digest>` fallback tag). Keyless v3 signatures
remain verifiable by cosign 2.6+; `.github/workflows/cosign-smoke.yml` checks
that on every change to the signing workflows and reports it in the run summary.

### Installing Cosign

```bash
brew install cosign  # macOS
# or see https://docs.sigstore.dev/cosign/system_config/installation/
cosign version       # 2.6 or newer (3.x recommended)
```

### Verifying an image

The quickest way is the Makefile target, which checks the signature and the SPDX SBOM attestation non-interactively and fails if cosign is missing or older than 2.6:

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

# SPDX SBOM attestation (use --type spdx with cosign 2.x for 2.1.0/2.2.0)
cosign verify-attestation --type spdxjson \
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
