# AcmeToCasino — Casino Mathematics & Game Economy

Code from the AcmeToCasino Game Aggregation Layer (GAL), as referenced in
Chapter 15 (Casino Mathematics & Game Economy).

## Files

- **rng.py** — Server-side CSPRNG using Python's `secrets` module. Generates 32 random
  bytes from OS entropy, converts to a float in [0, 1), and maps the result against an
  RTP-weighted win distribution with four tiers (small/medium/large/jackpot). Every call
  produces an SHA-256 audit hash for regulatory compliance.

- **service.py** — Full bet lifecycle: validate session, debit wallet (BET event),
  generate outcome via CSPRNG, credit wallet if win (WIN event), record the game round
  for audit, and update session aggregates. Uses the event-sourced wallet — no direct
  balance mutations.

- **models.py** — Pydantic models for game sessions, bet requests/results, RNG batch
  operations, and game configuration.

## How This Maps to Chapter 15

The chapter covers the mathematical foundations of casino games:

1. **House Edge & RTP** — The `generate_outcome()` method implements a configurable
   RTP target. The loss probability is derived from `1 - (rtp / avg_win_multiplier)`,
   ensuring the long-run payout converges to the target RTP.
2. **Win Distribution Tiers** — Four tiers (50% small wins at 1-2x, 30% medium at
   2-5x, 15% large at 5-20x, 5% jackpot at 20-100x) create realistic variance while
   maintaining the mathematical expectation.
3. **Bet Lifecycle** — `place_bet()` shows the complete flow from wager to settlement,
   including wallet debits/credits, RNG generation, and audit trail recording.
4. **Server-Side Control** — RTP is stored in the database (`rtp_configs` table),
   not in client-side code, preventing manipulation.
