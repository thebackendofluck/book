# 🎰 Casino Anti-Fraud KYC Verification System

## 📋 Overview

Production-ready anti-fraud system for online casino withdrawals with advanced KYC verification, biometric authentication, and regulatory compliance automation.

### Key Features
- 🔐 Military-grade encryption (DoD 5220.22-M)
- 👤 Biometric verification with anti-spoofing
- 📄 Multi-document OCR processing
- 🌍 International compliance (GDPR, AML, PSD2)
- 🤖 Machine learning risk scoring
- 📊 Real-time monitoring and alerts
- 🗑️ Automated secure document destruction

## 🏗️ Architecture

### Microservices
1. **Document Processor** - OCR, MRZ reading, security feature detection
2. **Encryption Service** - HSM integration, key rotation, secure storage
3. **Biometric Service** - Face matching, liveness detection, anti-spoofing
4. **Compliance Engine** - Sanctions screening, PEP checks, AML monitoring
5. **Secure Deletion** - GDPR-compliant data destruction
6. **ML Risk Scorer** - Fraud detection and risk assessment

### Technology Stack
- **Languages**: Python 3.11, SQL
- **Databases**: PostgreSQL 18, MongoDB 6, Redis 8
- **ML/AI**: TensorFlow, PyTorch, Face Recognition
- **Monitoring**: Prometheus, Grafana, ELK Stack
- **Security**: HSM, AES-256-GCM, TLS 1.3
- **Container**: Docker, Docker Compose

## 🚀 Quick Start

### Prerequisites
- Docker Engine 20.10+
- Docker Compose 2.0+
- 16GB RAM minimum
- 100GB storage
- GPU (optional, for ML acceleration)

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/thebackendofluck/book
cd book/19-anti-fraud-system-deep-dive/kyc-antifraud-system
```

2. **Configure environment**
```bash
cp env.template .env
# Edit .env with your configuration
```

3. **Generate secrets**
```bash
# Generate encryption key
openssl rand -base64 32

# Create HSM PIN secret
echo "your-hsm-pin" | docker secret create hsm_pin -
echo "your-hsm-admin-pin" | docker secret create hsm_admin_pin -
```

4. **Initialize database**
```bash
docker-compose up -d postgres-db
docker exec -i postgres-db psql -U postgres < init-scripts/01-schema.sql
```

5. **Start all services**
```bash
docker-compose up -d
```

6. **Verify deployment**
```bash
# Check service health
docker-compose ps

# View logs
docker-compose logs -f document-processor

# Access monitoring
open http://localhost:3000  # Grafana
open http://localhost:9090  # Prometheus
open http://localhost:5601  # Kibana
```

## 📖 API Documentation

### Document Upload
```http
POST /api/v1/kyc/upload
Content-Type: multipart/form-data

{
  "user_id": "string",
  "document_type": "passport|driver_license|national_id",
  "document": <file>
}
```

### Verification Status
```http
GET /api/v1/kyc/status/{verification_id}

Response:
{
  "status": "completed",
  "risk_level": "low",
  "risk_score": 0.15,
  "checks": {
    "document_authentic": true,
    "biometric_match": true,
    "sanctions_clear": true,
    "pep_check": false
  }
}
```

### GDPR Request
```http
POST /api/v1/gdpr/request
{
  "user_id": "string",
  "request_type": "access|deletion|portability"
}
```

## 🔧 Configuration

### Risk Thresholds
Thresholds are read from the environment (see `env.template`:
`FACE_MATCH_THRESHOLD`, `AML_THRESHOLD`, `DATA_RETENTION_DAYS`). The banding
below is the policy this service implements:
```yaml
risk_levels:
  low: 0.0 - 0.3
  medium: 0.31 - 0.7
  high: 0.71 - 0.9
  critical: 0.91 - 1.0

auto_approval:
  max_risk_score: 0.3
  min_document_quality: 0.7
  require_biometric: true
```

### Compliance Settings
```yaml
gdpr:
  retention_days: 90
  auto_deletion: true
  anonymization: true

aml:
  transaction_threshold: 10000
  velocity_check_period: 24h
  max_transactions_per_day: 10
```

## 🛡️ Security

### Encryption
- All data encrypted at rest (AES-256-GCM)
- TLS 1.3 for data in transit
- Hardware Security Module (HSM) for key management
- Automatic key rotation every 90 days

### Authentication & Authorization
- JWT tokens with 15-minute expiry
- Role-based access control (RBAC)
- Multi-factor authentication (MFA)
- API rate limiting

### Audit Trail
- Complete audit logging
- Tamper-proof log storage
- Real-time alerting
- Compliance reporting

## 📊 Monitoring

### Metrics
- Document processing rate
- Verification success rate
- Average processing time
- Risk score distribution
- Compliance check results

### Alerts
- High risk transactions
- Multiple failed verifications
- System performance degradation
- Security breaches
- Compliance violations

### Dashboards
Access Grafana at http://localhost:3000
- KYC Overview Dashboard
- Risk Analytics Dashboard
- Compliance Dashboard
- System Performance Dashboard

## 🧪 Testing

The test suite for Chapter 19 lives one level up, in `../tests/`, and runs
against a stack started with `docker-compose up -d`.

### Unit Tests
```bash
pytest ../tests/test_data_ingestion.py
```

### Integration Tests
```bash
pytest ../tests/integration/
```

## 📚 Documentation

The full design document for this system, covering every service, the API
surface, the GDPR and AML procedures, and the data retention policy, is
[`casino_antifraud_kyc_system.md`](./casino_antifraud_kyc_system.md) in this
directory. The narrative walkthrough is Chapter 19 of the book.

Per-service source lives in `document-processor/`, `encryption/`,
`biometric/`, `compliance/`, and `secure-deletion/`.

## 🚨 Troubleshooting

### Common Issues

**Document processing fails**
```bash
# Check OCR language packs
docker exec document-processor tesseract --list-langs

# Verify document quality
docker exec document-processor python check_quality.py /path/to/document
```

**Biometric matching errors**
```bash
# Check face detection models
docker exec biometric-service python -c "import face_recognition; print('OK')"

# Test with sample image
docker exec biometric-service python test_face.py
```

**HSM connection issues**
```bash
# Verify HSM connectivity
docker exec encryption-service pkcs11-tool --list-slots

# Test key generation
docker exec encryption-service python test_hsm.py
```

## 🤝 Support

This is companion code for the book *The Backend of Luck*. It is published as a
reference implementation, not as a hosted product, so there is no support desk,
uptime SLA, or chat community behind it.

- Questions and corrections: gustavo@thebackendofluck.com
- Book and errata: https://thebackendofluck.com
- Code and issues: https://github.com/thebackendofluck/book

## 📄 License

Licensing terms for this companion code are stated in the repository root of
https://github.com/thebackendofluck/book.

For licensing questions: gustavo@thebackendofluck.com

## 🏆 Compliance Frameworks Targeted

This reference implementation is designed against the controls below. It is not
itself certified or audited; certification applies to a deployed operator, not to
sample code.

- ISO 27001:2013 (Information Security)
- SOC 2 Type II (Security and Availability)
- PCI DSS Level 1 (Payment Card Security)
- GDPR (EU Data Protection)
- MGA remote gaming technical requirements (Malta Gaming Authority)

## 📈 Design Targets

These are the budgets the architecture is sized for, not measured results from a
production deployment.

- **Throughput**: 10+ documents/second
- **Accuracy**: 99.5% verification accuracy
- **Availability**: 99.99%
- **Latency**: < 3 seconds average processing
- **Scale**: 50+ million requests/month

## 🔄 Version History

### v2.0.0 (Current)
- Advanced biometric verification
- ML-based risk scoring
- Enhanced GDPR automation
- Multi-language OCR support

### v1.5.0
- HSM integration
- Sanctions screening
- PEP database checks

### v1.0.0
- Initial release
- Basic KYC verification
- Document upload and OCR

---

**Built with ❤️ for the online gaming industry**

*Ensuring safe, compliant, and fraud-free gaming experiences worldwide*
