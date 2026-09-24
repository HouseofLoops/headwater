# GitHub Actions Release Workflow Setup

This document explains how to configure the GitHub Actions release workflow for automatic Docker publishing.

## Required Repository Secrets

### Docker Hub Secrets

- **`DOCKERHUB_USERNAME`**: Your Docker Hub username
- **`DOCKERHUB_TOKEN`**: Your Docker Hub access token (not password)

### How to Create Docker Hub Token

1. Go to [Docker Hub](https://hub.docker.com/)
2. Sign in to your account
3. Go to **Account Settings** → **Security**
4. Click **New Access Token**
5. Give it a descriptive name (e.g., "GitHub Actions")
6. Copy the generated token

### GitHub Repository Secrets Setup

1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add the following secrets:

| Secret Name | Value | Description |
|-------------|-------|-------------|
| `DOCKERHUB_USERNAME` | Your Docker Hub username | Used for Docker Hub authentication |
| `DOCKERHUB_TOKEN` | Your Docker Hub token | Used for Docker Hub authentication |

## Workflow Features

### 🐳 Docker Publishing

- **Multi-architecture builds** (AMD64 + ARM64)
- **Docker Hub publishing** with version and latest tags
- **GitHub Packages publishing** with version and latest tags

### 🔒 Security Features

- **Container signing** with Cosign
- **SBOM generation** using Syft
- **Cryptographic attestations** for supply chain security

### 📋 Release Automation

- **Automatic version bumping** (patch increment)
- **Comprehensive release notes** with deployment information
- **GitHub release creation** with all artifacts

## Workflow Permissions

The workflow requires these permissions:

- `contents: write` - For creating releases and pushing commits
- `packages: write` - For publishing to GitHub Packages
- `id-token: write` - For OIDC token generation (required for Cosign)

## Testing the Workflow

To test the workflow:

1. Create a pull request to the `main` branch
2. Merge the pull request
3. The workflow will automatically:
   - Bump the version
   - Build and push Docker images
   - Sign the images
   - Generate SBOMs
   - Create a GitHub release

## Troubleshooting

### Common Issues

1. **"DOCKERHUB_USERNAME not found"**
   - Ensure the secret is created with the exact name `DOCKERHUB_USERNAME`
   - Check that it's in the repository secrets (not environment secrets)

2. **"Authentication failed"**
   - Verify your Docker Hub token is valid
   - Ensure your Docker Hub account has permission to push to the repository

3. **"Permission denied"**
   - Check that the workflow permissions are set correctly
   - Ensure the repository has GitHub Packages enabled

### Manual Testing

You can test Docker builds locally using the provided scripts:

```bash
# Build multi-architecture images
./scripts/docker_multiarch.sh push your-dockerhub-username

# Verify a published image's CI signature and SBOM attestation (requires cosign >= 3)
make docker-verify IMAGE=ghcr.io/rainmanjam/headwater TAG=<version>
```

Images are signed only by CI, keylessly (see below); locally built images are not signed and there is no signing key.

## Release Process

When a PR is merged to `main`:

1. **Version Management**
   - Current version is read from `app/__version__.py`
   - Version is incremented (patch level)
   - Changes are committed and pushed

2. **Docker Build & Publish**
   - Multi-architecture image is built (AMD64 + ARM64)
   - Images are pushed to Docker Hub and GitHub Packages
   - Both version tag and `latest` tag are created

3. **Security & Compliance**
   - Images are signed keylessly with Cosign by digest (Sigstore/Fulcio, GitHub OIDC; signer identity `https://github.com/rainmanjam/headwater/.github/workflows/release.yml@refs/heads/main`)
   - SBOM is generated and attached as a keyless SPDX attestation (`cosign attest --type spdx`)
   - Cryptographic provenance is created

4. **Release Creation**
   - GitHub release is created with comprehensive notes
   - Release includes deployment instructions
   - Security verification commands are provided
