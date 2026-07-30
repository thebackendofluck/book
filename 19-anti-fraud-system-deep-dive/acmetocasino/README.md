# AcmeToCasino — Anti-Fraud System

Code from the AcmeToCasino compliance module, as referenced in
Chapter 19 (Anti-Fraud System).

## Files

- **compliance_router.py** — FastAPI endpoints for KYC submission, verification
  (including auto-verify for demo environments), AML alert listing, AML alert review,
  and per-player risk scoring. RBAC-protected: KYC verification and alert review
  require the "operator" role.

- **compliance_service.py** — Service layer implementing:
  - **KYC workflow**: submit documents, verify/reject with PostgreSQL subquery
    pattern, update player kyc_status.
  - **AML velocity checks**: monitors deposits per hour, bets per minute, and
    24-hour deposit volumes. Auto-creates alerts when thresholds are breached.
  - **Alert management**: create, list (with status/severity filters), and review
    alerts with status transitions (resolved, escalated, false_positive).

## How This Maps to Chapter 19

The chapter covers anti-fraud and compliance systems in regulated gambling:

1. **KYC Pipeline** — The submit-verify workflow mirrors real-world document
   verification: documents enter as "pending", are reviewed by operators, and
   the player's kyc_status is updated atomically.
2. **Velocity-Based Detection** — `check_velocity()` implements three threshold
   tiers (deposits/hour, bets/minute, 24h volume) that auto-create AML alerts,
   demonstrating rule-based fraud detection before ML models are introduced.
3. **Alert Lifecycle** — Alerts flow through open -> {resolved, escalated,
   false_positive}, with reviewer attribution and timestamps for audit trails.
4. **RBAC Integration** — Only operators can verify KYC and review alerts,
   enforcing the principle of least privilege.
5. **Event Publishing** — All compliance actions publish Redis events for
   real-time dashboard monitoring.
