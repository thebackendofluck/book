# payments-techmojo -- Multi-Provider Payment Orchestration

Sanitised extracts from a production Play Framework payment service handling deposits
across Adyen, Trustly, EPG (Easy Payment Gateway), Interac, PXP, and other PSPs.

## Architecture

```
Player Cashier
      |
DepositController (Play action composition)
      |
DepositProcessor (method resolution, validation, PSP delegation)
      |
  +-------+-------+-------+-------+
  | Adyen | Trustly| EPG  | Interac| ... (each implements Payments trait)
  +-------+-------+-------+-------+
      |
  Kafka (DepositToAccount message)
      |
DepositConsumer (status update, matrix scores, confirmation email)
      |
  PaymentDAO (Oracle via Slick, >22 column mapping)
```

## Key Files

| File | Purpose |
|------|---------|
| `DepositController.scala` | HTTP endpoints: deposit, makePayment, paymentMethods, listDeposits, method ordering |
| `PaymentDAO.scala` | Slick DAO for USER_PAYMENTS with Oracle MERGE for owner tracking |
| `DepositToAccountProcessor.scala` | Kafka message bridge for async deposit-to-account crediting |
| `DepositConsumer.scala` | Pekko Connectors Kafka consumer with RestartSource, matrix score updates |
| `PaymentVO.scala` | Core domain model with composed types for >22 Slick columns |
| `Payments.scala` | PSP contract trait + PaymentProviderSettings with DB-backed config |
| `build.sbt` | Play Framework + Pekko Connectors Kafka + Slick + Adyen + BouncyCastle |

## Patterns Demonstrated

- **Trait-based PSP abstraction**: `Payments` trait with `startPaymentProcess` as the single contract
- **DB-backed configuration**: Provider + brand segmented settings, no redeployment for changes
- **Kafka event pipeline**: Deposit -> DepositToAccount -> DepositToAccountFinished -> matrix scores
- **User-level locking**: `UserConcurrency.locks.withLock` prevents double-deposit race conditions
- **Action composition**: PaymentAction -> RequestInstrumentAction -> KamonInstrumentAction -> TransformToPaymentAction
- **Oracle MERGE**: Atomic upsert for payment card owner AML tracking
- **>22 column workaround**: Slick composed case classes (FailureInfo, PaymentProviderInfo)
