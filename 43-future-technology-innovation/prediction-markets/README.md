# Chapter 43c — Prediction Markets Reference Implementation

Companion code for **Chapter 43c: Prediction Markets — Where Trading Meets
Betting**. Four stdlib-only modules, each mirroring one layer of a
prediction-market stack:

| Module | Layer |
|--------|-------|
| `order_book.py` | Binary-contract CLOB: price-time priority matching, integer-cent prices (1–99), NO→YES normalization, self-trade prevention, positions/open interest |
| `market_lifecycle.py` | Market state machine (DRAFT→…→SETTLED/VOIDED), resolution criteria, quorum-based attestation oracle, dispute window, settlement/void refunds |
| `jurisdiction_gate.py` | Jurisdiction × category distribution policy (DIRECT / PARTNER_EMBEDDED / BLOCKED / PENDING_FRAMEWORK) — snapshot of the July 2026 public map |
| `sportsbook_bridge.py` | Odds ↔ contract-price conversion, overround extraction, sportsbook-vs-exchange cost comparison, cross-venue discrepancy detection |
| `partner_hub_gateway.py` | Pattern 1 (embedded hub): HMAC-signed pseudonymous session handoff, idempotent host-side wallet bridge, host-boundary category filter |
| `partner_onboarding.py` | Phased partner-onboarding state machine (PRE_CONTRACT → SANDBOX → CERTIFICATION → GO_LIVE) with evidence gates + audit history |
| `tenant_provisioning.py` | Pattern 2 (B2B platform): per-tenant vertical enablement validated against the tenant licence's jurisdiction gate, versioned config distribution |
| `venue_liquidity_bridge.py` | Pattern 3 (co-brand): display-only cross-venue price mirroring vs. blocked cross-regime order routing (compatibility matrix as data) |

All money is integer cents. No third-party dependencies.

## Tests

```bash
cd writing/new-book/scripts
uv run pytest chapter-43/prediction-markets/tests/ -q
```
