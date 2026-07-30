# Wasabi Backup — Jurisdiction Compliance Guide

This guide explains WHY each jurisdiction maps to a specific Wasabi region
and documents the regulatory citations backing each decision.  Read this
before changing any bucket region or adding a new jurisdiction.

---

## Why Wasabi?

Wasabi is S3-compatible object storage with ~80% lower cost than AWS S3
and no egress fees.  For regulated iGaming operators holding 7-year
compliance archives, the savings are material:

| Storage | Cost/TB/month | Egress per GB | 7-year cost per 100 TB |
|---------|--------------|---------------|------------------------|
| AWS S3 Standard | ~$23 | ~$0.09 | ~$193K |
| AWS S3 Glacier | ~$4 | ~$0.01 (retrieval) | ~$34K |
| Wasabi | ~$6.99 | $0 | ~$59K |

Wasabi's flat-rate pricing with no retrieval fees makes it preferable to
Glacier for compliance archives that regulators may request on short notice.

The S3-compatible API means the same `aws` CLI and `boto3` code works
unchanged; only the `--endpoint-url` and region differ.

---

## Jurisdiction-to-Region Mapping

### MGA (Malta Gaming Authority) → `eu-central-1` (Amsterdam, Netherlands)

**Legal basis:**
- GDPR Article 32 requires appropriate technical measures to ensure data
  security.  Personal data of EU/EEA residents must remain within the EEA
  under GDPR Chapter V (transfer rules).
- MGA Technical Standards for Gaming Devices §8 mandates audit trail
  retention for 7 years.

**Why Amsterdam:**
Wasabi `eu-central-1` is the primary EU region.  Amsterdam sits within the
EEA; Dutch law applies the GDPR directly.

**Secondary allowed:**
Cross-EEA replication to `eu-central-2` (Frankfurt, Germany) is permitted
because Germany is an EEA member state.  Data does not leave the EEA.

**Retention:** 2,555 days (7 years)

---

### UKGC (UK Gambling Commission) → `eu-west-1` (London, UK)

**Legal basis:**
- Post-Brexit, the UK retained GDPR as "UK GDPR" via the European Union
  (Withdrawal) Act 2018.  International transfers require either a UK
  adequacy regulation or a valid UK International Data Transfer Agreement
  (IDTA).
- UKGC LCCP Social Responsibility Code 15.2.1 requires AML records to be
  retained for 5 years.

**Why London:**
Wasabi `eu-west-1` is physically located in London.  Data on UK soil is
not an "international transfer" under UK GDPR — no adequacy assessment
or IDTA is required.

**Secondary allowed:** NO
There is no other UK-soil Wasabi region.  Replicating to any EU region
(Paris, Frankfurt, etc.) would constitute a UK GDPR international transfer
requiring a UK adequacy regulation or IDTA.  Neither is straightforward
for Wasabi bucket-to-bucket replication.  If a secondary is operationally
required, obtain a signed IDTA with Wasabi and document it.

**Retention:** 1,825 days (5 years — AML floor; 3-year general floor + margin)

---

### DGE NJ (New Jersey Division of Gaming Enforcement) → `us-east-1` (Ashburn, VA)

**Legal basis:**
- N.J.A.C. 13:69O-1.2: all internet gaming systems must be physically
  located in Atlantic City, New Jersey.
- N.J.A.C. 13:69D-1.13(d): financial records must be retained for 7 years.
- N.J.A.C. 13:69E-1: game records must be retained for 5 years.

**Critical warning:**
Wasabi `us-east-1` is in Ashburn, Virginia — NOT Atlantic City.  This region
is used ONLY for encrypted compliance archives and requires prior written
authorisation from the DGE.  Live transactional data, active player records,
and primary backups MUST remain in Atlantic City data centres.

Do not interpret the presence of `us-east-1` in this configuration as
permission to move primary NJ data outside Atlantic City.

**Secondary allowed:** NO — NJ data cannot cross state lines under any circumstance.

**Retention:** 2,555 days (7 years — most restrictive NJ requirement)

---

### PGCB PA (Pennsylvania Gaming Control Board) → `us-east-1` (Ashburn, VA)

**Legal basis:**
- 58 Pa. Code §441a.7: records must be retained for 7 years.
- Pennsylvania Gaming Control Board Technical Standards §1180a.3.
- PGCB explicitly permits interstate backup with AES-256 encryption.

**Why us-east-1:**
Pennsylvania does not mandate a specific city or state.  Wasabi `us-east-1`
(Virginia) is physically close and legally acceptable.

**Secondary allowed:** YES → `us-east-2` (Manassas, Virginia).  Both are US
soil; no interstate transfer issue for Pennsylvania.

**Retention:** 2,555 days (7 years)

---

### MGCB MI (Michigan Gaming Control Board) → `us-east-1` (Ashburn, VA)

**Legal basis:**
- Mich. Admin. Code R 432.632: records must be retained for 5 years.
- MGCB Internet Gaming Rules §432.654.
- Out-of-state DR and cloud storage explicitly approved with encryption
  and annual security assessment.

**Why us-east-1:**
Michigan is the most cloud-friendly US gambling jurisdiction.  The MGCB
accepts out-of-state cloud storage for DR and archive with documented
encryption.  Wasabi `us-east-1` satisfies this requirement.

**Secondary allowed:** YES → `us-east-2` (Manassas, Virginia).

**Retention:** 1,825 days (5 years)

---

### AGCO ON (Alcohol and Gaming Commission of Ontario) → `ca-central-1` (Toronto, Canada)

**Legal basis:**
- AGCO Rules Respecting iGaming §7.4: records must be retained for 7 years.
- AGCO iGO Technical Standards §8.1.3: all player data must reside on
  Canadian soil.  Offshore storage requires written AGCO approval — which
  is routinely denied for player PII and financial records.
- PIPEDA (Personal Information Protection and Electronic Documents Act):
  personal data transferred outside Canada must receive equivalent
  protection.  AGCO interprets this as requiring domestic residency for
  player data without explicit consent.

**Why Toronto:**
Wasabi `ca-central-1` is the only Canadian Wasabi region.  There is no
alternative.

**Secondary allowed:** NO — AGCO prohibits offshore backup.  There is no
second Canadian Wasabi region to replicate to.  If AGCO relaxes this rule,
a second Wasabi CA region would be the appropriate target.

**Retention:** 2,555 days (7 years)

---

### PAGCOR (Philippine Amusement and Gaming Corporation) → `ap-southeast-1` (Singapore)

**Legal basis:**
- PAGCOR Offshore Gaming License §4.3: records must be retained for 5 years.
- PAGCOR Charter §15.2: offshore storage of gaming data requires prior
  written PAGCOR approval.

**Critical warning:**
Wasabi does not offer a Philippine region.  The Singapore region is the
nearest geographically, but it is still an "offshore" location under
Philippine law.  This configuration is valid ONLY after obtaining written
PAGCOR authorisation for Singapore-hosted encrypted archives.  Primary
infrastructure and local backups must remain in the Philippines.

**Secondary allowed:** NO

**Retention:** 1,825 days (5 years)

---

## How to Add a New Jurisdiction

1. Research the governing framework's data residency rules and retention
   requirements.  Find the specific statute/regulation citations.

2. Identify the closest Wasabi region whose physical location satisfies
   the residency requirement.  Check https://wasabi.com/cloud-storage-pricing/
   for the current region list and confirmed physical locations.

3. Add a `WasabiJurisdictionConfig` entry to `wasabi-jurisdiction-config.py`,
   including:
   - The `legal_basis` field with full regulatory citations
   - `cross_region_replica_allowed` based on whether the regulator permits
     replication to another region in the same territory
   - `retention_days` set to the most demanding applicable requirement

4. Add the jurisdiction to the `APPROVED_PRIMARY_REGIONS` map in
   `wasabi-backup.sh` and `RTO_TARGETS_MINUTES` in `wasabi-restore-test.sh`.

5. Create a `/etc/igaming/wasabi-<jurisdiction>.env` file on each server
   that runs backups for that jurisdiction.

6. Run `./wasabi-backup.sh check-region` to confirm the guardrail fires
   correctly for a misconfigured region.

---

## Encryption Architecture

All archives are encrypted at two layers before leaving the server:

1. **Client-side (openssl AES-256-CBC, pbkdf2 key derivation)**
   Applied by `backup_manager.sh` before the file is handed to this script.
   The encryption key is stored in `/etc/igaming/backup-encryption.key`
   and should be managed via HashiCorp Vault (see `encryption/disk/get_luks_key.py`).

2. **Server-side (Wasabi SSE-S3, AES-256)**
   Applied on upload via `--sse AES256` in the AWS CLI call.  This encrypts
   the data at rest in Wasabi even if client-side encryption were somehow
   stripped.

Key management responsibility is split: the operator holds the client-side
key (Vault); Wasabi holds the server-side key (SSE-S3).  Neither party can
read the plaintext without the other's key.  This satisfies the GDPR
Article 32 "appropriate technical measures" requirement and the typical
iGaming regulator's encryption-at-rest mandate.

---

## Operational Runbook

### Monthly DR drill checklist

```
[ ] Run ./wasabi-restore-test.sh full-drill
[ ] Record total elapsed time against RTO target
[ ] Confirm all validation queries passed
[ ] File the result JSON in the compliance audit folder
[ ] If RTO exceeded: document root cause and remediation plan
```

### Quarterly compliance verification

```
[ ] Run ./wasabi-backup.sh check-region for each jurisdiction
[ ] Verify bucket lifecycle rules match retention requirements
    (./wasabi-backup.sh lifecycle-apply --dry-run)
[ ] Confirm no cross-jurisdiction object keys exist
    (./wasabi-backup.sh list for each jurisdiction — check prefixes)
[ ] Review IAM policies: confirm jurisdiction-specific Wasabi keys have
    no cross-bucket access
[ ] Check Wasabi region list for new regions that might better serve
    a jurisdiction
```

### Responding to a regulator's data access request

Wasabi supports pre-signed URLs for time-limited, read-only access.
To provide a regulator with access to specific backup objects:

```bash
AWS_ACCESS_KEY_ID=$WASABI_ACCESS_KEY \
AWS_SECRET_ACCESS_KEY=$WASABI_SECRET_KEY \
aws --endpoint-url "$WASABI_ENDPOINT" \
    s3 presign "s3://$WASABI_BUCKET/$OBJECT_KEY" \
    --expires-in 86400   # 24 hours
```

Provide the pre-signed URL in writing.  Log the access grant in the audit trail.
