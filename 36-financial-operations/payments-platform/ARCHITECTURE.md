# Payments Platform — Architecture Notes

## Source Analysis

**Historical source baseline:** Play Framework 2.12 / Scala 2.12 / Java 8
**Current chapter runtime:** FastAPI / Python 3.12
**Database:** PostgreSQL-compatible persistence model
**Messaging:** Kafka-style payment lifecycle events
**PSPs represented in the chapter runtime:**

| PSP | Methods | Deposit | Withdrawal |
|-----|---------|---------|------------|
| Adyen | Card, Apple Pay, Google Pay | Yes | No |
| PayPal | PayPal, Billing Agreements | Yes | No |
| Braintree | Card, PayPal via vault | Yes | No |
| Trustly | Bank Transfer (EU) | Yes | Yes |
| PIX | Instant Bank Transfer (BR) | Yes | No |
| Boleto | Bank Slip (BR) | Yes | No |
| Neteller | E-wallet | Yes | Yes |
| Skrill | E-wallet | Yes | Yes |
| EPG (Easy Payment Gateway) | Card | Yes | No |
| Hexopay | Card | Yes | No |
| PXP Financial | Card, Apple Pay, PayNearMe | Yes | No |
| Zimpler | Bank | Yes | No |
| Sightline | Card | Yes | No |
| VIP Preferred / Global Payments | Card, Offline | Yes | No |
| Interac | Bank (CA) | Yes | No |
| APCO | e-Wallet | Yes | No |
| OchaPay | Card | Yes | No |
| UpayS | Card | Yes | No |

**PIX**, **Boleto** (not in source — added for Brazilian market completeness)
**Neteller**, **Skrill** (inferred from Paysafe-style wallet support)

---

## Key Architectural Decisions in the Python Migration

### 1. Framework choice: FastAPI over Django/Flask

The original Play Framework is inherently async (Akka, Futures). FastAPI with
`asyncio` + `httpx.AsyncClient` preserves this property. Django would require
forcing synchronous wrappers around PSP HTTP calls.

### 2. Pydantic v2 models replace case classes

Scala case classes map cleanly to Pydantic `BaseModel`. Immutability is
preserved via `model_copy(update=...)` — the same pattern as the Scala
`.copy()` method.

### 3. State machine as an explicit class

The original PaymentStatus Java enum carried `isTerminal` / `isLocking` flags
but transition validation was scattered across service methods. The Python
`PaymentStateMachine` and `WithdrawalStateMachine` classes centralise all
valid transitions in a single lookup table, making the lifecycle auditable
and testable in isolation.

### 4. PSP Router with ordered failover

The original Scala code used Guice DI to inject a named `PaymentProvider`
per method. The Python `PSPRouter` replaces this with an explicit `RoutingRule`
list that is readable, testable, and hot-reloadable without restarting the
process.

### 5. Fraud scoring as a pre-transaction gate

The original service had risk checks spread across the `DepositProcessor` and
separate platform calls. The Python `FraudChecker` is a self-contained
in-process scorer (< 5 ms) that runs before any PSP call and returns a
structured `FraudScore`. Production deployments would call an external ML
service instead.

### 6. Currency amounts as integers (minor units)

Both the original Java/Scala and the Python implementation store amounts as
integers in the smallest currency unit (cents, pence). This avoids all
floating-point rounding issues during processing, settlement, and
reconciliation.

---

## File Map

```
payments-platform/
├── main.py               FastAPI app, route wiring, DI bootstrap
├── models.py             Core domain models (Payment, Deposit, Withdrawal, ...)
├── state_machine.py      PaymentStateMachine + WithdrawalStateMachine
├── deposit_service.py    Deposit orchestration (validate → fraud → PSP)
├── withdrawal_service.py Withdrawal orchestration (KYC → review → PSP payout)
├── psp_router.py         PSP selection + ordered failover
├── fraud_check.py        Pre-transaction fraud scoring engine
├── reconciliation.py     Daily PSP settlement reconciliation
├── checkout_assets.py    Hosted checkout branding and label bundle generator
├── checkout_embed.py     Embeddable cashier manifest generator
├── export_openapi.py     OpenAPI export for client and docs generation
├── psp/
│   ├── base.py           PSPAdapter abstract base class
│   ├── adyen.py          Adyen Checkout v71 adapter
│   ├── paypal.py         PayPal Orders API v2 adapter
│   ├── braintree.py      Braintree SDK adapter
│   ├── trustly.py        Trustly JSON-RPC 1.1 adapter
│   ├── pix.py            PIX (Brazil) instant payment adapter
│   ├── boleto.py         Boleto bancario adapter
│   └── neteller.py       Neteller / Paysafe e-wallet adapter
├── tests/
│   └── test_payments.py  39 unit / integration tests
├── requirements.txt
└── Dockerfile
```

---

## Consolidation Notes

- Legacy chapter artifacts implemented in Scala, Ruby, Angular, and NPM tooling
  were retired from the book workspace.
- Hosted checkout skin management is represented by `checkout_assets.py`.
- Cashier launcher and iframe embedding are represented by `checkout_embed.py`.
- OpenAPI publication is represented by `export_openapi.py`.
- The chapter now uses Python as the single implementation language for payment
  orchestration and adjacent operational tooling.
- `main.py` keeps the application locally runnable with in-memory services and
  demo PSP registration, while the concrete adapters under `psp/` document the
  provider-facing integration contracts.
