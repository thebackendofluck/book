# AcmeToCasino — FinOps & Monitoring

Code from the AcmeToCasino observability stack, as referenced in
Chapter 30 (FinOps Deep Dive).

## Files

- **metrics.py** — Centralized Prometheus metric definitions: 11 metrics covering
  HTTP requests (counter + histogram), active players (gauge), wallet events (counter
  by type), game rounds (counter + bet amount histogram by game), RNG calls (counter),
  AML alerts (counter by type/severity), KYC checks (counter by status), WebSocket
  connections (gauge), and Redis pub/sub messages (counter by channel).

- **prometheus.yml** — Prometheus scrape configuration with two jobs: the main API
  metrics endpoint (10s interval) and the health endpoint (30s interval). Both target
  the FastAPI container within the Docker network.

## How This Maps to Chapter 30

The chapter covers FinOps practices for iGambling infrastructure:

1. **Metric Categories** — The metrics span all six domains (PAM, Wallet, GAL,
   Compliance, Responsible Gaming, Game Control), enabling cost attribution per
   business function.
2. **RED Method** — HTTP request rate, error rate (via status labels), and duration
   (histogram with custom buckets) implement the RED methodology for service monitoring.
3. **Business Metrics** — `game_rounds_bet_amount` and `wallet_events_total` directly
   track revenue-generating activity, connecting infrastructure costs to business value.
4. **Resource Awareness** — The Prometheus config uses tight scrape intervals (10s)
   with 7-day retention and 512MB storage limit, showing cost-conscious monitoring.
5. **Custom Buckets** — Bet amount histogram uses casino-specific buckets (0.10 to
   1000), not generic defaults, enabling meaningful percentile analysis of wagering
   patterns.
