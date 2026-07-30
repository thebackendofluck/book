# AWS CloudHSM vs YubiHSM 2 — Feature and Architecture Comparison
## iGaming Platform — HSM Selection Guide

---

## Summary

Both AWS CloudHSM and YubiHSM 2 hold **FIPS 140-2 Level 3** certification — the same cryptographic assurance level. The choice between them is driven by deployment model, cost, and operational complexity.

| Decision Factor | Prefer YubiHSM 2 | Prefer AWS CloudHSM |
|----------------|------------------|---------------------|
| Infrastructure | Bare metal / on-premises | AWS-native |
| Budget | Cost-sensitive | Cloud-native OpEx model |
| Team skills | Linux sysadmin | AWS IAM / DevOps |
| Compliance | Needs physical custody | Needs AWS audit trail |
| HA requirements | Can manage dual USB devices | Needs automatic failover |
| Regulatory | Physical key ceremony required | Accepts AWS shared responsibility |

---

## Detailed Feature Matrix

| Feature | YubiHSM 2 | AWS CloudHSM |
|---------|-----------|-------------|
| **FIPS 140-2 Level** | Level 3 | Level 3 |
| **Hardware** | Yubico ATECC608 (USB dongle) | Cavium/Marvell LiquidSecurity (Luna Network HSM 7) |
| **Form factor** | USB 2.0 Type A (thumb drive size) | 19" rack appliance (AWS-managed) |
| **Acquisition cost** | ~£550 per device | £0 (usage-based) |
| **Ongoing cost** | Negligible (power only) | ~$1.60/hour per HSM (~£1,400/month × 2) |
| **3-year TCO** | ~£4,000–6,000 | ~£60,000–70,000 |
| **Minimum for HA** | 2 devices (manual management) | 2 HSMs (auto-managed by cluster) |

### Cryptographic Operations

| Operation | YubiHSM 2 | AWS CloudHSM | Notes |
|-----------|-----------|-------------|-------|
| **AES-256-GCM** | No (via PKCS#11) | Yes | YubiHSM 2 fw 2.4.1 does not expose CKM_AES_GCM (`0x1087`) via PKCS#11; CloudHSM supports it natively |
| **AES-256 key wrap** | Yes (CKM_AES_CCM, proprietary) | Yes (CKM_AES_KEY_WRAP_PAD) | YubiHSM uses AES-CCM for wrap internally; CKM_AES_GCM absent from PKCS#11 shim |
| **ECDSA P-256** | Yes | Yes | Both: CKM_ECDSA |
| **ECDSA P-384** | Yes | Yes | — |
| **Ed25519** | Yes | Yes (SDK 5.11+, Q4 2023) | CloudHSM: verify SDK version |
| **RSA-4096** | Yes | Yes | — |
| **HKDF-SHA256** | Yes (native CKM_SP800_108) | Partial (CKM_SP800_108_COUNTER_KDF) | See note below |
| **TRNG** | Yes (hardware, FIPS validated) | Yes (hardware, FIPS validated) | Both use hardware entropy |
| **HMAC-SHA256** | Yes | Yes | — |
| **AES-CBC** | Yes | Yes | — |

**HKDF note:** Both devices support NIST SP 800-108 counter-mode KDF. The platform's RFC 5869 HKDF-SHA256 derivation (used for player-scoped epoch key expansion) runs in software, using the HSM-resident epoch key as the IKM (input key material). The seed material never leaves the HSM; only derived output material is used in application memory.

### Interface and Connectivity

| Attribute | YubiHSM 2 | AWS CloudHSM |
|-----------|-----------|-------------|
| **PKCS#11** | Yes (`yubihsm_pkcs11.so`) | Yes (`libcloudhsm_pkcs11.so`) |
| **PKCS#11 slot** | 0 | 1 |
| **Transport** | USB → localhost:12345 (HTTP) | VPC ENI (TCP 2223–2225, TLS) |
| **Network boundary** | Same host only | VPC private subnets |
| **JCE / KMIP / CNG** | No | Yes (CloudHSM JCE, KMIP 1.1, CNG) |
| **AWS KMS custom key store** | No | Yes |
| **SDK** | YubiHSM SDK 2.x (`yubihsm-setup`) | CloudHSM Client SDK 5.x |

### OpenBao Seal Configuration Differences

| Setting | YubiHSM 2 | AWS CloudHSM |
|---------|-----------|-------------|
| `lib` | `/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so` | `/opt/cloudhsm/lib/libcloudhsm_pkcs11.so` |
| `slot` | `"0"` | `"1"` |
| `mechanism` | `"0x1087"` (CKM_AES_GCM) | `"0x00001085"` (CKM_AES_KEY_WRAP_PAD) |
| `pin` | `env:BAO_HSM_PIN` (format: `0001:<password>`) | `env:CLOUDHSM_PIN` (format: `hsm-app:<password>`) |
| `key_label` | `bao-root-key-aes` | `wrap-key-aes256` |
| Daemon required | Yes (`yubihsm-connector`) | Yes (`cloudhsm-client`) |

### High Availability and Resilience

| Attribute | YubiHSM 2 | AWS CloudHSM |
|-----------|-----------|-------------|
| **HA mechanism** | Manual: 2 devices on separate hosts | Automatic: cluster sync across AZs |
| **Failover time** | Minutes (requires operator action) | Seconds (automatic) |
| **Key sync between devices** | Backup + restore (manual) | Automatic (continuous within cluster) |
| **Node failure detection** | Application-level (no built-in) | CloudWatch HsmCount metric |
| **Geographic DR** | Manual backup export to offline media | Cluster backup → S3 → restore in new region |

### Key Management and Lifecycle

| Attribute | YubiHSM 2 | AWS CloudHSM |
|-----------|-----------|-------------|
| **Key export** | Encrypted backup only | Encrypted backup only |
| **Key destruction** | `yubihsm-shell deleteobject` | `key_mgmt_util deleteKey` |
| **Key audit trail** | Local connector logs | CloudWatch Logs (per API operation) |
| **Key listing** | `yubihsm-shell listobjects` | `key_mgmt_util listKeys` |
| **Backup format** | YubiHSM proprietary (AES-CCM) | CloudHSM proprietary (AWS-managed) |
| **Backup destination** | Local filesystem or S3 | S3 (via `backupHSM`) |
| **Cross-device key sharing** | Via wrap/unwrap objects | Via cluster (automatic) |

### Security Model

| Attribute | YubiHSM 2 | AWS CloudHSM |
|-----------|-----------|-------------|
| **Physical custody** | Operator controls the device | AWS controls the hardware (shared responsibility) |
| **Multi-tenancy** | Single-tenant (dedicated USB) | Single-tenant (dedicated HSM per cluster) |
| **Authentication** | Key ID + password | CU username + password (PKCS#11) |
| **Roles** | Application (auth key), Operator, Auditor | Crypto Officer (CO), Crypto User (CU), Appliance User (AU) |
| **PIN complexity** | Configurable | Min 7 chars; PCI DSS recommends ≥ 12 |
| **Tamper evidence** | Physical tamper evident housing | AWS physical security (SOC 1/2/3, ISO 27001) |
| **FIPS module** | YubiHSM 2 FIPS (L3) | Luna Network HSM 7 (L3) |

### Operations and Maintenance

| Attribute | YubiHSM 2 | AWS CloudHSM |
|-----------|-----------|-------------|
| **Firmware updates** | Manual (`yubihsm-shell upgrade`) | AWS-managed (no customer action) |
| **Hardware replacement** | Order new device, restore backup | AWS handles (transparent to operator) |
| **Monitoring** | Custom scripts or Prometheus exporter | CloudWatch native metrics + alarms |
| **Access logging** | Connector logs (local) | CloudTrail + CloudWatch Logs |
| **Key ceremony** | Physical presence required | Remote (AWS console or CLI) |
| **Compliance evidence** | Operator-generated logs | AWS CloudTrail (tamper-evident) |

---

## Mechanism ID Reference

### YubiHSM 2 (PKCS#11)

| Operation | Mechanism ID | Mechanism Name |
|-----------|-------------|----------------|
| AES-GCM wrap | `0x1087` | `CKM_AES_GCM` — **not available in fw 2.4.1** |
| AES-CBC | `0x1022` | `CKM_AES_CBC` |
| ECDSA | `0x1041` | `CKM_ECDSA` |
| ECDSA-SHA256 | `0x1044` | `CKM_ECDSA_SHA256` |
| Ed25519 | `0x80000040` | `CKM_EDDSA` |
| HKDF | `0x80000040` | (custom extension) |

### AWS CloudHSM SDK 5.x (PKCS#11)

| Operation | Mechanism ID | Mechanism Name |
|-----------|-------------|----------------|
| AES key wrap | `0x00001085` | `CKM_AES_KEY_WRAP_PAD` |
| AES-GCM | `0x00001087` | `CKM_AES_GCM` |
| ECDSA | `0x00001041` | `CKM_ECDSA` |
| ECDSA-SHA256 | `0x00001044` | `CKM_ECDSA_SHA256` |
| Ed25519 | `0x80000040` | `CKM_EDDSA` (SDK 5.11+) |
| SP 800-108 KDF | `0x0000402F` | `CKM_SP800_108_COUNTER_KDF` |
| RSA PKCS | `0x00000001` | `CKM_RSA_PKCS` |

---

## Migration Notes

### From YubiHSM 2 to CloudHSM

Keys cannot be migrated between HSM vendors (FIPS constraint — no plaintext export). Migration requires:

1. Generate new keys in CloudHSM
2. Stand up new OpenBao cluster with CloudHSM seal
3. Re-issue all DEKs (Transit key rotation + re-encryption of stored data)
4. Update application `BAO_ADDR` to new OpenBao NLB endpoint
5. Decommission old cluster after validation period

Estimated migration effort: 1–2 engineer-weeks.

### From CloudHSM to YubiHSM 2 (reverse)

Same process in reverse. Use `setup-openbao-cluster.sh` from `../yubihsm-setup/`.

---

## Compliance Equivalence

Both YubiHSM 2 FIPS and AWS CloudHSM satisfy the same regulatory requirements:

| Standard | Requirement | YubiHSM 2 | CloudHSM |
|---------|------------|-----------|---------|
| PCI DSS 4.0.1 | 3.7.1 — Key generation in FIPS L3 device | Yes | Yes |
| PCI DSS 4.0.1 | 3.7.3 — Key storage in FIPS L3 device | Yes | Yes |
| GLI-19 | 7.2 — Non-exportable RNG seed keys | Yes | Yes |
| ISO 27001:2022 | A.8.24 — Use of cryptography | Yes | Yes |
| SOC 2 Type II | Availability / Confidentiality | Manual HA | Native HA |

**Key difference for PCI DSS assessors:** With YubiHSM 2, the QSA can inspect the physical device and verify its presence. With CloudHSM, the AWS FIPS attestation certificate and CloudTrail audit logs substitute for physical inspection. Both are acceptable under PCI DSS 4.0.1.

---

## Recommendation Matrix

| Scenario | Recommendation |
|----------|---------------|
| New deployment, AWS-only | CloudHSM |
| Existing bare metal, no AWS | YubiHSM 2 |
| Hybrid (AWS + on-prem) | YubiHSM 2 on-prem + CloudHSM on AWS (separate clusters) |
| Cost-sensitive startup | YubiHSM 2 (significantly lower TCO) |
| Enterprise with SRE team | CloudHSM (reduced operational burden) |
| Regulatory requiring physical control | YubiHSM 2 |
| Multi-region AWS DR | CloudHSM (cross-region cluster restore) |
| Development / testing | YubiHSM 2 (or software emulation) |

---

## References

- AWS CloudHSM User Guide: https://docs.aws.amazon.com/cloudhsm/latest/userguide/
- AWS CloudHSM PKCS#11 Mechanisms: https://docs.aws.amazon.com/cloudhsm/latest/userguide/pkcs11-mechanisms.html
- YubiHSM 2 PKCS#11 SDK: https://developers.yubico.com/YubiHSM2/Component_Reference/PKCS_11/
- OpenBao PKCS#11 Seal: https://openbao.org/docs/configuration/seal/pkcs11/
- NIST FIPS 140-2: https://csrc.nist.gov/publications/detail/fips/140/2/final
- CloudHSM FIPS Certificate: https://csrc.nist.gov/projects/cryptographic-module-validation-program/certificate/3254
