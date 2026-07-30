# Payments Platform — Architecture Notes

## Source Analysis

**Source:** Play Framework 2.12 / Scala 2.12 / Java 8
**Size:** ~853 files
**Database:** PostgreSQL (Slick ORM)
**Messaging:** Kafka (deposit lifecycle events, chargebacks)
**PSPs identified in source:**

| PSP | Methods | Deposit | Withdrawal |
|-----|---------|---------|------------|
| Adyen | Card, Apple Pay, Google Pay | Yes | No |
| PayPal | PayPal, Billing Agreements | Yes | No |
| Braintree | Card, PayPal via vault | Yes | No |
| Trustly | Bank Transfer (EU) | Yes | Yes |
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

**PIX** (not in source — added for Brazilian market completeness)
**Neteller** (inferred from Paysafe references)

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
├── psp/
│   ├── base.py           PSPAdapter abstract base class
│   ├── adyen.py          Adyen Checkout v71 adapter
│   ├── paypal.py         PayPal Orders API v2 adapter
│   ├── braintree.py      Braintree SDK adapter
│   ├── trustly.py        Trustly JSON-RPC 1.1 adapter
│   ├── pix.py            PIX (Brazil) instant payment adapter
│   └── neteller.py       Neteller / Paysafe e-wallet adapter
├── tests/
│   └── test_payments.py  25 unit / integration tests
├── requirements.txt
└── Dockerfile
```

---

## What was NOT migrated (out of scope for book chapter)

- **Admin UI** — Scala Play controllers for the back-office dashboard
- **Groovy payment scripts** — dynamic per-brand settings evaluated at runtime
- **Kafka consumer loop** — `DepositConsumer`, `ChargebackConsumer` (would be Celery/aiokafka workers)
- **Per-brand / per-jurisdiction configuration engine** — replaced by `RoutingRule` stubs
- **Full DB schema + Alembic migrations** — shape is derivable from model classes
- **3-D Secure v2 flow in full detail** — represented as VERIFY state with callback endpoint
