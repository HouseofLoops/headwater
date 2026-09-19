# Security Guidelines

This document outlines security best practices and guidelines for the Headwater API.

## Table of Contents

- [API Key Security](#api-key-security)
- [Data Protection](#data-protection)
- [Network Security](#network-security)
- [Infrastructure Security](#infrastructure-security)
- [Monitoring and Logging](#monitoring-and-logging)
- [Incident Response](#incident-response)
- [Compliance](#compliance)
- [Third-Party Dependencies](#third-party-dependencies)

## API Key Security

### Best Practices

- **Store API keys securely**: Use environment variables, secret management systems, or secure key vaults
- **Rotate API keys regularly**: Change keys at least quarterly to minimize exposure
- **Use different API keys for different environments**: Separate keys for development, staging, and production
- **Never commit API keys to version control**: Use `.gitignore` and pre-commit hooks to prevent accidental commits
- **Monitor API key usage**: Track usage patterns to detect suspicious activity

### Key Management

```bash
# Environment variables (recommended)
export HEADWATER_API_KEY="your_production_key_here"

# .env file (development only)
HEADWATER_API_KEY=your_development_key_here

# Docker secrets
echo "your_api_key" | docker secret create headwater_api_key -
```

### Rate Limiting

- **Default limits**: 100 requests per hour per API key
- **Configurable limits**: Adjust based on your plan and usage patterns
- **Automatic blocking**: Suspicious patterns trigger temporary blocks
- **Rate limit headers**: All responses include usage information

```bash
# Check rate limit status
curl -I -H "x-api-key: your_key" https://api.headwater.com/health

# Response headers
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1631548800
```

## Data Protection

### Input Validation

- **Comprehensive validation**: All inputs are validated and sanitized
- **SQL injection prevention**: Parameterized queries prevent injection attacks
- **XSS protection**: Output encoding prevents cross-site scripting
- **File upload restrictions**: Strict limits on file types and sizes

### Input Sanitization

```python
# Example input validation
from pydantic import BaseModel, validator
from typing import Optional

class NewsSearchRequest(BaseModel):
    q: str
    country: Optional[str] = "US"
    language: Optional[str] = "en"
    max_results: Optional[int] = 10

    @validator('q')
    def validate_query(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('Query cannot be empty')
        if len(v) > 500:
            raise ValueError('Query too long')
        return v.strip()

    @validator('max_results')
    def validate_max_results(cls, v):
        if v < 1 or v > 100:
            raise ValueError('max_results must be between 1 and 100')
        return v
```

### Output Sanitization

- **Sensitive data filtering**: Never expose internal system information
- **Error message sanitization**: Generic error messages prevent information leakage
- **Response data validation**: Ensure responses match expected schemas

## Network Security

### HTTPS/TLS

- **TLS 1.3 required**: All production deployments must use HTTPS
- **Valid certificates**: Use certificates from trusted Certificate Authorities (CAs)
- **HSTS headers**: Enable HTTP Strict Transport Security
- **Certificate pinning**: Optional additional security layer

### HTTPS Configuration

```nginx
# Nginx HTTPS configuration
server {
    listen 443 ssl http2;
    server_name api.headwater.com;

    ssl_certificate /path/to/certificate.crt;
    ssl_certificate_key /path/to/private.key;
    ssl_protocols TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;

    # HSTS
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Firewall Configuration

- **Restrict access**: Only open necessary ports
- **Use security groups**: Cloud-specific network security
- **Web Application Firewall (WAF)**: Protection against common web attacks
- **DDoS protection**: Rate limiting and traffic filtering

## Infrastructure Security

### Container Security

- **Minimal base images**: Use Alpine Linux or distroless images
- **Regular security scanning**: Scan images for vulnerabilities
- **Non-root users**: Run containers as non-privileged users
- **Resource limits**: Prevent resource exhaustion attacks

### Dockerfile Security Best Practices

```dockerfile
# Use minimal base image
FROM python:3.9-alpine

# Create non-root user
RUN addgroup -g 1001 -S appuser && \
    adduser -S -D -H -u 1001 -h /app -s /sbin/nologin -G appuser -g appuser appuser

# Install dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["python", "main.py"]
```

### Kubernetes Security

- **RBAC**: Role-Based Access Control for cluster access
- **Network policies**: Control pod-to-pod communication
- **Security contexts**: Define security settings for pods
- **Regular updates**: Keep Kubernetes and dependencies updated

### Kubernetes Security Configuration

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: headwater-api
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1001
    runAsGroup: 1001
    fsGroup: 1001
  containers:
  - name: api
    image: headwater/api:latest
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop:
        - ALL
    resources:
      limits:
        cpu: "1"
        memory: "1Gi"
      requests:
        cpu: "500m"
        memory: "512Mi"
```

## Monitoring and Logging

### Security Monitoring

- **Log authentication attempts**: Track all login and API key usage
- **Monitor for anomalies**: Detect unusual patterns or suspicious activity
- **Alert on security events**: Immediate notifications for security incidents
- **Regular security audits**: Periodic review of security controls

### Audit Logging

- **Comprehensive logging**: Log all API requests with context
- **Secure log storage**: Encrypted and access-controlled log storage
- **Log retention**: Configurable retention periods
- **Log analysis**: Tools for analyzing security events

### Monitoring Configuration

```python
# Security monitoring with Prometheus
from prometheus_client import Counter, Histogram

# Authentication metrics
AUTH_ATTEMPTS = Counter(
    'auth_attempts_total',
    'Total authentication attempts',
    ['result', 'method']
)

AUTH_FAILURES = Counter(
    'auth_failures_total',
    'Total authentication failures',
    ['reason']
)

# Request metrics
REQUESTS_TOTAL = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status', 'user_agent']
)

# Suspicious activity detection
SUSPICIOUS_REQUESTS = Counter(
    'suspicious_requests_total',
    'Total suspicious requests',
    ['type', 'ip_address']
)
```

## Incident Response

### Security Incident Procedure

1. **Detection**
   - Monitor alerts and logs for security events
   - Automated detection of suspicious patterns
   - User reports of security issues

2. **Assessment**
   - Evaluate impact and scope of the incident
   - Determine affected systems and data
   - Assess potential damage and risks

3. **Containment**
   - Isolate affected systems
   - Block malicious traffic
   - Preserve evidence for investigation

4. **Recovery**
   - Restore systems from clean backups
   - Apply security patches
   - Monitor for reoccurrence

5. **Lessons Learned**
   - Document the incident and response
   - Update security procedures
   - Implement preventive measures

### Incident Response Team

- **Security Lead**: Overall incident coordination
- **Technical Team**: System analysis and recovery
- **Legal Team**: Compliance and notification requirements
- **Communications**: Internal and external communications

### Communication Plan

- **Internal notifications**: Immediate team alerts
- **Customer notifications**: As required by incident severity
- **Regulatory reporting**: Compliance with legal requirements
- **Public statements**: When necessary for transparency

## Compliance

### GDPR Compliance

- **Data minimization**: Only collect necessary data
- **Right to erasure**: Implement data deletion capabilities
- **Consent management**: Clear consent for data processing
- **Data processing records**: Maintain detailed processing logs

### GDPR Implementation

```python
# Data deletion endpoint
@app.delete("/api/v1/user-data/{user_id}")
async def delete_user_data(user_id: str, current_user: User = Depends(get_current_user)):
    """Delete all user data as per GDPR right to erasure"""

    # Verify user owns the data
    if current_user.id != user_id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    # Delete user data
    await delete_user_records(user_id)
    await delete_api_keys(user_id)
    await delete_search_history(user_id)

    # Log deletion for audit
    logger.info(f"User data deleted for user {user_id}")

    return {"message": "User data deleted successfully"}
```

### SOC 2 Compliance

- **Security controls**: Documented security procedures
- **Regular audits**: Third-party security assessments
- **Incident response**: Documented and tested procedures
- **Change management**: Controlled system changes

### Other Compliance Standards

- **ISO 27001**: Information security management
- **PCI DSS**: Payment card data security (if applicable)
- **HIPAA**: Health data protection (if applicable)

## Third-Party Dependencies

### Dependency Management

- **Regular updates**: Keep dependencies current
- **Vulnerability scanning**: Automated security scanning
- **License compliance**: Ensure compatible licenses
- **Minimal dependencies**: Reduce attack surface

### Security Scanning

```bash
# Scan for vulnerabilities
pip install safety
safety check

# Alternative: use pip-audit
pip install pip-audit
pip-audit

# Docker image scanning
docker scan headwater/api:latest

# GitHub Dependabot
# Enable in .github/dependabot.yml
```

### Dependency Update Process

1. **Automated PRs**: Dependabot creates update PRs
2. **Security review**: Review changes for security implications
3. **Testing**: Run full test suite with updated dependencies
4. **Deployment**: Gradual rollout with monitoring

### Google Services Security

- **API key restrictions**: Limit keys to specific services
- **IP address restrictions**: Whitelist allowed IP addresses
- **Usage monitoring**: Track API usage and costs
- **Secure credential storage**: Encrypted storage of service account keys

### Google Cloud Key Management

```python
# Google Cloud KMS for API key encryption
from google.cloud import kms_v1

def encrypt_api_key(api_key: str) -> bytes:
    """Encrypt API key using Google Cloud KMS"""
    client = kms_v1.KeyManagementServiceClient()
    name = client.crypto_key_path(project, location, key_ring, crypto_key)

    response = client.encrypt(request={"name": name, "plaintext": api_key.encode()})
    return response.ciphertext

def decrypt_api_key(encrypted_key: bytes) -> str:
    """Decrypt API key using Google Cloud KMS"""
    client = kms_v1.KeyManagementServiceClient()
    name = client.crypto_key_path(project, location, key_ring, crypto_key)

    response = client.decrypt(request={"name": name, "ciphertext": encrypted_key})
    return response.plaintext.decode()
```

## Security Checklist

### Development Phase

- [ ] Use secure coding practices
- [ ] Implement input validation
- [ ] Use parameterized queries
- [ ] Implement proper error handling
- [ ] Regular security code reviews

### Deployment Phase

- [ ] Secure configuration management
- [ ] Environment-specific configurations
- [ ] Secret management system
- [ ] Network security controls
- [ ] Monitoring and alerting setup

### Operations Phase

- [ ] Regular security updates
- [ ] Vulnerability scanning
- [ ] Incident response procedures
- [ ] Security monitoring
- [ ] Regular backups and testing

## Contact Information

### Security Issues

- **Report vulnerabilities**: security@headwater.com
- **PGP Key**: [Download PGP public key](https://headwater.com/pgp-key.txt)
- **Response time**: Within 24 hours for critical issues

### Security Team

- **Security Lead**: security@headwater.com
- **Incident Response**: incident@headwater.com
- **Compliance**: compliance@headwater.com

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [Google Cloud Security](https://cloud.google.com/security)
- [Kubernetes Security Best Practices](https://kubernetes.io/docs/concepts/security/)

---

*This document is regularly updated. Last reviewed: September 14, 2025*
