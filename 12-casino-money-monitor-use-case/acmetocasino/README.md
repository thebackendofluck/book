# AcmeToCasino — Real-Time Cash Flow Monitoring

Code from the AcmeToCasino event-sourced wallet, as referenced in
Chapter 12 (Real-Time Cash Flow Monitoring).

## Files

- **wallet_router.py** — FastAPI endpoints for balance queries, transaction creation,
  and event history. All mutations go through `create_event()` — there is no endpoint
  that directly updates a balance field.

- **wallet_service.py** — Event-sourced wallet service. Balance is computed as
  `SUM(credits) - SUM(debits)` on every read. Credits include DEPOSIT, WIN, and
  BONUS_CREDIT; debits include BET, WITHDRAWAL, and BONUS_DEBIT. Insufficient balance
  checks happen at debit time by computing the current balance first.

- **wallet_models.py** — Pydantic models defining the six wallet event types, the
  transaction request schema, wallet event response, and computed balance response.

## How This Maps to Chapter 12

The chapter covers real-time cash flow monitoring in iGambling platforms:

1. **Event Sourcing** — The wallet never stores a balance directly. Every financial
   action appends an immutable event to `wallet_events`. Balance is always derived,
   making the system auditable and tamper-evident.
2. **Append-Only Ledger** — No UPDATE or DELETE on wallet_events. The INSERT-only
   pattern means the complete financial history is preserved for regulatory reporting.
3. **Real-Time Events** — Every wallet mutation publishes a Redis Pub/Sub message
   (e.g., `wallet.deposit`, `wallet.bet`), which is forwarded to WebSocket clients
   for live dashboard updates.
4. **Prometheus Metrics** — The `wallet_events_total` counter tracks event volumes
   by type, enabling real-time monitoring of deposit/withdrawal rates.
5. **Concurrency Safety** — The `get_balance()` check before debits prevents
   double-spend scenarios in concurrent bet placement.
