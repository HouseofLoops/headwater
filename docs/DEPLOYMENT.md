# Deployment Guide

This document provides step-by-step instructions for deploying the Headwater API in various environments.

## Prerequisites

Before deploying the Headwater API, ensure you have the following:

- Docker and Docker Compose (for local and container-based deployments)
- Kubernetes cluster (for production deployments)
- Google API credentials (see [GOOGLE_SERVICES.md](GOOGLE_SERVICES.md))
- Redis instance (optional, for caching and rate limiting)
- PostgreSQL database (optional, for persistent storage)

## Local Development Deployment

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/headwater.git
cd headwater
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit the `.env` file with your configuration:

```
# API settings
API_KEYS=your_api_key_1,your_api_key_2
ENABLE_API_KEY_AUTH=true

# Rate limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_TIMEFRAME=3600

# Caching
ENABLE_CACHE=true
CACHE_TTL=3600
REDIS_URL=redis://localhost:6379/0

# Application settings
DEBUG=true
ENVIRONMENT=development
PROJECT_NAME=Headwater
VERSION=1.0.0
DESCRIPTION=API for social media data aggregation and analysis
```

### 3. Build and Run with Docker Compose

```bash
docker-compose up -d
```

This will start the following services:
- Headwater API on port 8000
- Redis on port 6379 (if configured)
- PostgreSQL on port 5432 (if configured)

### 4. Verify Deployment

```bash
curl http://localhost:8000/health
```

You should see a response like:

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "environment": "development",
  "timestamp": 1622548800.123456
}
```

## Production Deployment with Docker

### 1. Build the Docker Image

```bash
docker build -t headwater:1.0.0 .
```

### 2. Run the Container

```bash
docker run -d \
  --name headwater \
  -p 8000:8000 \
  -e API_KEYS=your_api_key_1,your_api_key_2 \
  -e ENABLE_API_KEY_AUTH=true \
  -e RATE_LIMIT_ENABLED=true \
  -e RATE_LIMIT_REQUESTS=100 \
  -e RATE_LIMIT_TIMEFRAME=3600 \
  -e ENABLE_CACHE=true \
  -e CACHE_TTL=3600 \
  -e REDIS_URL=redis://redis:6379/0 \
  -e DEBUG=false \
  -e ENVIRONMENT=production \
  -e PROJECT_NAME="Headwater" \
  -e VERSION=1.0.0 \
  -e DESCRIPTION="API for social media data aggregation and analysis" \
  headwater:1.0.0
```

## Production Deployment with Kubernetes

### 1. Create Kubernetes Secrets

```bash
kubectl create namespace headwater

kubectl create secret generic headwater-secrets \
  --namespace headwater \
  --from-literal=API_KEYS=your_api_key_1,your_api_key_2 \
  --from-literal=REDIS_URL=redis://redis:6379/0 \
  --from-literal=DATABASE_URL=postgresql://user:password@postgres:5432/headwater
```

### 2. Create Kubernetes ConfigMap

```bash
kubectl create configmap headwater-config \
  --namespace headwater \
  --from-literal=ENABLE_API_KEY_AUTH=true \
  --from-literal=RATE_LIMIT_ENABLED=true \
  --from-literal=RATE_LIMIT_REQUESTS=100 \
  --from-literal=RATE_LIMIT_TIMEFRAME=3600 \
  --from-literal=ENABLE_CACHE=true \
  --from-literal=CACHE_TTL=3600 \
  --from-literal=DEBUG=false \
  --from-literal=ENVIRONMENT=production \
  --from-literal=PROJECT_NAME="Headwater" \
  --from-literal=VERSION=1.0.0 \
  --from-literal=DESCRIPTION="API for social media data aggregation and analysis"
```

### 3. Deploy Redis (if needed)

```yaml
# redis-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: headwater
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:6.2-alpine
        ports:
        - containerPort: 6379
        resources:
          limits:
            cpu: "0.5"
            memory: "512Mi"
          requests:
            cpu: "0.2"
            memory: "256Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: headwater
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
```

Apply the Redis deployment:

```bash
kubectl apply -f redis-deployment.yaml
```

### 4. Deploy the Headwater API

```yaml
# headwater-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: headwater
  namespace: headwater
spec:
  replicas: 3
  selector:
    matchLabels:
      app: headwater
  template:
    metadata:
      labels:
        app: headwater
    spec:
      containers:
      - name: headwater
        image: headwater:1.0.0
        ports:
        - containerPort: 8000
        envFrom:
        - configMapRef:
            name: headwater-config
        - secretRef:
            name: headwater-secrets
        resources:
          limits:
            cpu: "1"
            memory: "1Gi"
          requests:
            cpu: "0.5"
            memory: "512Mi"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: headwater
  namespace: headwater
spec:
  selector:
    app: headwater
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
```

Apply the Headwater API deployment:

```bash
kubectl apply -f headwater-deployment.yaml
```

### 5. Create Ingress for External Access

```yaml
# headwater-ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: headwater-ingress
  namespace: headwater
  annotations:
    kubernetes.io/ingress.class: nginx
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - api.headwater.com
    secretName: headwater-tls
  rules:
  - host: api.headwater.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: headwater
            port:
              number: 80
```

Apply the Ingress:

```bash
kubectl apply -f headwater-ingress.yaml
```

## Continuous Integration / Continuous Deployment (CI/CD)

### GitHub Actions Workflow Example

Create a file at `.github/workflows/deploy.yml`:

```yaml
name: Deploy Headwater API

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.9'
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install pytest pytest-cov
    - name: Test with pytest
      run: |
        pytest --cov=app tests/

  build:
    needs: test
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
    - uses: actions/checkout@v2
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v1
    - name: Login to DockerHub
      uses: docker/login-action@v1
      with:
        username: ${{ secrets.DOCKERHUB_USERNAME }}
        password: ${{ secrets.DOCKERHUB_TOKEN }}
    - name: Build and push
      uses: docker/build-push-action@v2
      with:
        context: .
        push: true
        tags: yourusername/headwater:latest,yourusername/headwater:${{ github.sha }}

  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
    - uses: actions/checkout@v2
    - name: Set up kubectl
      uses: azure/setup-kubectl@v1
    - name: Set Kubernetes context
      uses: azure/k8s-set-context@v1
      with:
        kubeconfig: ${{ secrets.KUBE_CONFIG }}
    - name: Update deployment image
      run: |
        kubectl set image deployment/headwater headwater=yourusername/headwater:${{ github.sha }} -n headwater
        kubectl rollout status deployment/headwater -n headwater
```

## Monitoring and Logging

### Prometheus and Grafana Setup

1. Install Prometheus Operator:

```bash
kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/main/bundle.yaml
```

2. Create a ServiceMonitor for Headwater API:

```yaml
# headwater-service-monitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: headwater
  namespace: headwater
spec:
  selector:
    matchLabels:
      app: headwater
  endpoints:
  - port: http
    path: /metrics
    interval: 15s
```

Apply the ServiceMonitor:

```bash
kubectl apply -f headwater-service-monitor.yaml
```

### ELK Stack for Logging

1. Install Elasticsearch, Logstash, and Kibana using Helm:

```bash
helm repo add elastic https://helm.elastic.co
helm repo update

helm install elasticsearch elastic/elasticsearch -n logging --create-namespace
helm install kibana elastic/kibana -n logging
helm install logstash elastic/logstash -n logging
```

2. Configure Filebeat to collect logs:

```yaml
# filebeat-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: filebeat-config
  namespace: logging
data:
  filebeat.yml: |-
    filebeat.inputs:
    - type: container
      paths:
        - /var/log/containers/headwater-*.log
      processors:
        - add_kubernetes_metadata:
            host: ${NODE_NAME}
            matchers:
            - logs_path:
                logs_path: "/var/log/containers/"

    output.elasticsearch:
      hosts: ["elasticsearch-master:9200"]
```

Apply the ConfigMap:

```bash
kubectl apply -f filebeat-config.yaml
```

3. Deploy Filebeat:

```yaml
# filebeat-deployment.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: filebeat
  namespace: logging
spec:
  selector:
    matchLabels:
      app: filebeat
  template:
    metadata:
      labels:
        app: filebeat
    spec:
      serviceAccountName: filebeat
      containers:
      - name: filebeat
        image: docker.elastic.co/beats/filebeat:7.15.0
        args: ["-c", "/etc/filebeat.yml", "-e"]
        volumeMounts:
        - name: config
          mountPath: /etc/filebeat.yml
          subPath: filebeat.yml
        - name: varlibdockercontainers
          mountPath: /var/lib/docker/containers
          readOnly: true
        - name: varlog
          mountPath: /var/log
          readOnly: true
        env:
        - name: NODE_NAME
          valueFrom:
            fieldRef:
              fieldPath: spec.nodeName
      volumes:
      - name: config
        configMap:
          name: filebeat-config
      - name: varlibdockercontainers
        hostPath:
          path: /var/lib/docker/containers
      - name: varlog
        hostPath:
          path: /var/log
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: filebeat
  namespace: logging
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: filebeat
rules:
- apiGroups: [""]
  resources:
  - namespaces
  - pods
  verbs:
  - get
  - list
  - watch
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: filebeat
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: filebeat
subjects:
- kind: ServiceAccount
  name: filebeat
  namespace: logging
```

Apply the Filebeat deployment:

```bash
kubectl apply -f filebeat-deployment.yaml
```

## Troubleshooting Deployment Issues

### Common Issues

1. **API not starting**:
   - Check logs: `kubectl logs deployment/headwater -n headwater`
   - Verify environment variables: `kubectl describe pod -l app=headwater -n headwater`

2. **Cannot connect to Redis**:
   - Check Redis service: `kubectl get svc redis -n headwater`
   - Verify Redis is running: `kubectl get pods -l app=redis -n headwater`

3. **API key authentication failing**:
   - Verify API keys in secrets: `kubectl get secret headwater-secrets -n headwater -o yaml`
   - Check `ENABLE_API_KEY_AUTH` setting in ConfigMap

4. **Health checks failing**:
   - Check health endpoint: `kubectl port-forward svc/headwater 8000:80 -n headwater` then `curl http://localhost:8000/health`
   - Verify dependencies are available (Redis, database)

### Deployment Checklist

- [ ] Environment variables configured correctly
- [ ] Secrets and ConfigMaps created
- [ ] Redis deployed and running (if used)
- [ ] Database deployed and running (if used)
- [ ] API deployment successful
- [ ] Service created and accessible
- [ ] Ingress configured correctly
- [ ] TLS certificates provisioned
- [ ] Monitoring and logging set up
