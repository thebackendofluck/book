# AcmeToCasino — Responsible Gaming

Code from the AcmeToCasino responsible gaming module, as referenced in
Chapter 26 (Responsible Gaming).

## Files

- **responsible_gaming_router.py** — FastAPI endpoints for setting deposit limits,
  self-exclusion, listing excluded players, and retrieving a player's full responsible
  gaming status (limits + exclusion + reality check).

- **responsible_gaming_service.py** — Service layer implementing:
  - **Deposit limits**: daily, weekly, monthly caps with automatic deactivation of
    previous limits for the same period. Enforcement via `check_limits()` which queries
    actual deposit sums within the interval.
  - **Self-exclusion**: player-initiated lockout for 1 to 3650 days. Sets player
    status to "excluded" to block all gameplay.
  - **Reality checks**: snapshot of session activity (total bet, total win, net
    position, session duration) combined with active limits and exclusion status.

- **responsible_gaming_models.py** — Pydantic models for deposit limit requests/responses,
  self-exclusion requests/responses, reality check snapshots, and the composite
  responsible gaming status.

## How This Maps to Chapter 26

The chapter covers responsible gaming requirements in regulated markets:

1. **Deposit Limits** — Configurable per period (daily/weekly/monthly) with
   enforcement at deposit time. The system checks actual wallet event sums against
   the configured cap, not a simple counter.
2. **Self-Exclusion** — Time-bounded lockout with start/end timestamps. The player's
   account status is updated to "excluded", which blocks game session creation in
   the GAL module.
3. **Reality Checks** — Combines session metrics (duration, bet/win totals) with
   limit and exclusion status into a single view, enabling the frontend to display
   periodic reminders.
4. **Cooling-Off Period** — Limits use `effective_at` timestamps, supporting
   regulatory requirements for delayed activation of reduced limits.
5. **Event-Driven** — All responsible gaming actions publish Redis events for
   operator dashboards and audit trails.
