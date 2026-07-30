<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-05.jpg" alt="Volume 5" width="150" /></a>

# Chapter 22: Internal Docker Registry: Why You Need Your Own and How to Build It

**📔 Part of Volume 5 — Infrastructure, Datacenter, and Deployment** · €49.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GYYG1HZ3) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 22 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Enterprise-grade Docker registry infrastructure for iGaming platforms with comprehensive security scanning, automated maintenance, and regulatory compliance.

## Directory Structure

```
scripts/chapter-22/
├── README.md                        # This file
├── registry-management/             # Python modules (~1,800 lines)
│   ├── __init__.py                 # Module exports
│   ├── security.py                 # Authentication, RBAC, TLS
│   ├── maintenance.py              # Health checks, cleanup
│   ├── version_manager.py          # Automated updates
│   ├── scanner.py                  # Trivy/Grype/Podman/QEMU
│   └── aqua_integration.py         # Enterprise Aqua Security
├── terraform/
│   ├── aws/main.tf                 # VPC, EKS, S3, ECR, NLB
│   └── kubernetes/main.tf          # Registry deployment, Trivy
├── docker/
│   ├── docker-compose.yml          # Local development stack
│   ├── setup.sh                    # Certificate generation
│   └── prometheus.yml              # Monitoring config
└── ansible/                        # Security hardening playbooks
```

## Components

### Python Modules

| Module | Lines | Purpose |
|--------|-------|---------|
| `security.py` | ~350 | htpasswd authentication, RBAC policies, TLS configuration |
| `maintenance.py` | ~400 | Async health checks, image cleanup, garbage collection |
| `version_manager.py` | ~400 | Docker Hub API, automated updates, backup/rollback |
| `scanner.py` | ~650 | Trivy/Grype integration, Podman, QEMU scanning |
| `aqua_integration.py` | ~350 | Enterprise Aqua Security client |

### Security Scanner Features

**Free Options (Open Source):**
- **Trivy**: Comprehensive vulnerability, secret, and config scanning
- **Grype**: Fast vulnerability detection from Anchore
- **ClamAV**: Malware detection for container filesystems

**Enterprise Options:**
- **Aqua Security**: Runtime protection, policy enforcement, compliance

**Container Runtime Support:**
- Docker images
- Podman images (rootless containers)
- QEMU/libvirt VM images (via guestmount)

### Terraform Infrastructure

**AWS Components (~$800-1,200/month):**

| Component | Configuration | Monthly Cost |
|-----------|---------------|--------------|
| EKS Cluster | v1.28, 2-4 m6i.large nodes | ~$350 |
| S3 Storage | 500GB, KMS encrypted | ~$15 |
| ElastiCache | Redis 8, t3.medium | ~$60 |
| NLB | TLS termination | ~$25 |
| NAT Gateway | Single AZ | ~$45 |
| ECR | Mirror repository | ~$5 |

## Quick Start

### Local Development

```bash
# Setup certificates and authentication
cd scripts/chapter-22/docker
chmod +x setup.sh
./setup.sh

# Start the registry stack
docker-compose up -d

# Test registry access
docker login registry.local:5000 -u admin
docker tag nginx:latest registry.local:5000/nginx:latest
docker push registry.local:5000/nginx:latest
```

### Security Scanning

```python
from registry_management import RegistrySecurityScanner, ScannerType

# Scan with Trivy (free)
scanner = RegistrySecurityScanner(
    registry_url='https://registry.local:5000',
    scanner_type=ScannerType.TRIVY
)

result = await scanner.scan_image('myapp:latest')
print(f"Risk: {result.risk_level}, Score: {result.risk_score}")
print(f"Critical CVEs: {result.vulnerabilities_summary.get('critical', 0)}")

# Scan Podman image
podman_result = await scanner.scan_podman_image('localhost/myapp:latest')

# Scan QEMU VM image
qemu_result = await scanner.scan_qemu_image('/var/lib/libvirt/images/vm.qcow2')
```

### Enterprise Scanning (Aqua Security)

```python
from registry_management import AquaSecurityClient

client = AquaSecurityClient(
    server_url='https://aqua-server:443',
    username='admin',
    password='password'
)

await client.authenticate()
result = await client.scan_image('registry.local', 'myapp', 'v1.0')

# Enforce policies
action = await client.enforce_policies(result)
if action.action == 'BLOCK':
    print(f"Deployment blocked: {action.reason}")
```

## Terraform Deployment

### AWS Infrastructure

```bash
cd scripts/chapter-22/terraform/aws

# Initialize
terraform init

# Plan
terraform plan -var="environment=production"

# Apply
terraform apply -var="environment=production" -auto-approve

# Get outputs
terraform output eks_cluster_endpoint
terraform output registry_storage_bucket
```

### Kubernetes Resources

```bash
cd scripts/chapter-22/terraform/kubernetes

# Configure kubectl
aws eks update-kubeconfig --name registry-igaming-eks

# Apply Kubernetes resources
terraform init
terraform apply \
    -var="s3_bucket=$(terraform -chdir=../aws output -raw registry_storage_bucket)" \
    -var="redis_endpoint=$(terraform -chdir=../aws output -raw redis_endpoint)" \
    -var="admin_password=your-secure-password"
```

## Regulatory Compliance

| Framework | Requirement | Implementation |
|-----------|-------------|----------------|
| **PCI-DSS** | 6.2 Vulnerability scanning | Automated Trivy/Aqua scanning |
| **PCI-DSS** | 7.1 Access control | RBAC policies, htpasswd auth |
| **PCI-DSS** | 10.x Logging | Audit trails for all operations |
| **SOX** | Integrity | KMS encryption, image signing |
| **GDPR** | Data protection | Secret scanning, no PII in images |
| **ISO 27001** | A.12.6 Technical vulnerability | Continuous scanning pipeline |
| **NIST 800-190** | Container security | CIS benchmarks, runtime policies |

## Cost Comparison

### Self-Hosted vs Cloud

| Solution | Setup | Monthly | Annual |
|----------|-------|---------|--------|
| AWS EKS + S3 | $2,000 | $900 | $12,800 |
| Docker Hub Team | $0 | $1,500 | $18,000 |
| AWS ECR only | $0 | $500 | $6,000 |
| Harbor on-premise | $10,000 | $300 | $13,600 |

### Security Tool Licensing

| Tool | Type | Cost |
|------|------|------|
| Trivy | Free, Open Source | $0 |
| Grype | Free, Open Source | $0 |
| ClamAV | Free, Open Source | $0 |
| Aqua Security | Enterprise | $50,000-200,000/yr |
| Podman | Free, Apache 2.0 | $0 |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         AWS Cloud                                │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │   NLB + TLS     │    │  Route 53       │                     │
│  │   (Port 443)    │    │  DNS            │                     │
│  └────────┬────────┘    └─────────────────┘                     │
│           │                                                      │
│  ┌────────┴────────────────────────────────────────────────┐    │
│  │                    EKS Cluster                           │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │    │
│  │  │  Registry   │  │  Registry   │  │   Trivy     │      │    │
│  │  │  Pod 1      │  │  Pod 2      │  │   Scanner   │      │    │
│  │  └──────┬──────┘  └──────┬──────┘  └─────────────┘      │    │
│  │         │                │                               │    │
│  └─────────┴────────────────┴───────────────────────────────┘    │
│                    │                                             │
│  ┌─────────────────┴─────────────────────┐                      │
│  │              S3 Storage               │                      │
│  │           (KMS Encrypted)             │                      │
│  └───────────────────────────────────────┘                      │
│                                                                  │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │   ElastiCache   │    │      ECR        │                     │
│  │   Redis Cache   │    │   (Mirror)      │                     │
│  └─────────────────┘    └─────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

## Integration with Other Chapters

- **Chapter 23**: DevSecOps pipeline integration
- **Chapter 24**: Security and compliance frameworks
- **Chapter 17**: RNG system containerization
- **Chapter 18**: RTC module container deployment

## Troubleshooting

### Common Issues

**Certificate Trust Error:**
```bash
# Copy CA certificate to Docker daemon
sudo mkdir -p /etc/docker/certs.d/registry.local:5000
sudo cp certs/registry.crt /etc/docker/certs.d/registry.local:5000/ca.crt
sudo systemctl restart docker
```

**Trivy Database Update:**
```bash
# Manual database update
trivy image --download-db-only

# Clear cache
rm -rf /root/.cache/trivy
```

**QEMU Image Mount Error:**
```bash
# Install libguestfs-tools
sudo apt-get install libguestfs-tools

# Verify
guestmount --version
```

## Requirements

- Python 3.9+
- Docker 24.0+ or Podman 4.0+
- Terraform 1.5+
- kubectl 1.28+
- AWS CLI v2

### Python Dependencies

```bash
pip install aiohttp boto3 pyyaml
```

## Contributing

Refer to the main book repository for contribution guidelines.
