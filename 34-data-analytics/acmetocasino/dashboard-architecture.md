# AcmeToCasino Dashboard Architecture

The dev platform dashboard (dashboard.html, ~3500 lines) is a single-page application
with 17 tabs, real-time WebSocket updates, and full API integration.

## Tab Structure

| # | Tab | Data Source | Key Features |
|---|-----|------------|--------------|
| 1 | **Overview** | /stats, /health | Player profile, balance, online players table, game performance chart |
| 2 | **Players** | /players | Player list with status indicators (Playing/Online/Idle), search, pagination |
| 3 | **Game History** | /gal/rounds | Recent game rounds with bet/win amounts, RNG hashes, RTP values |
| 4 | **Wallet** | /wallet/recent-events | Event-sourced ledger view, transaction types, real-time balance computation |
| 5 | **VIP Club** | /players (filtered) | VIP tier cards (Bronze/Silver/Gold/Platinum/Diamond) with shine animation |
| 6 | **Responsible Gaming** | /responsible-gaming/* | Deposit limits, self-exclusion list, reality check snapshots |
| 7 | **KYC** | /compliance/kyc/pending | Pending document reviews, approval/rejection workflow |
| 8 | **Compliance** | /compliance/aml/alerts | AML alerts with severity levels, review actions |
| 9 | **Analytics** | /stats (computed) | Revenue metrics, player activity trends, game popularity |
| 10 | **Infrastructure** | /health | Service grid, SSL certificates, system resources (Disk/Memory/CPU) |
| 11 | **Withdrawals** | /wallet/recent-events | Withdrawal-specific view with status tracking |
| 12 | **Payments** | Simulated | Payment gateway health (8 gateways), transaction volume pie chart, fraud metrics |
| 13 | **Game Control** | /game-control/rtp | Per-game RTP configuration with slider controls, real-time adjustment |
| 14 | **FinOps** | Simulated | Cloud cost monitoring, budget alerts, resource optimization suggestions |
| 15 | **User Journey** | Simulated | Visual session flow (login -> game -> bet -> win) with animated paths |
| 16 | **Fraud Detection** | Simulated | Full fraud pipeline (detection -> investigation -> resolution) |
| 17 | **Game Licensing** | Simulated | License tracking per game, regulatory compliance status |

## API Endpoints Used

### Authentication
- `POST /auth/login` — JWT login (15min access, 30d refresh tokens)

### Real-Time
- `WS /ws` — WebSocket connection for live event streaming from Redis Pub/Sub channels:
  - `player.events` — Registration, status changes
  - `wallet.transactions` — Deposits, bets, wins, withdrawals
  - `game.rounds` — Round completions with outcomes
  - `compliance.alerts` — KYC/AML events

### Data Endpoints
- `GET /health` — DB pool status, Redis memory, service statuses, uptime
- `GET /stats` — Aggregate player/wallet/game/compliance statistics
- `GET /wallet/recent-events` — Latest wallet events across all players
- `GET /wallet/{player_id}/balance` — Computed balance from event ledger
- `GET /gal/rounds` — Recent game rounds with RNG audit data
- `GET /game-control/rtp` — Current RTP configurations per game
- `PUT /game-control/rtp/{game_slug}` — Update RTP target for a game
- `GET /compliance/aml/alerts` — AML alerts with status/severity filters
- `GET /compliance/kyc/pending` — Pending KYC document reviews
- `GET /responsible-gaming/excluded` — Currently self-excluded players

## Frontend Architecture

- **No framework** — Vanilla JavaScript with DOM manipulation
- **CSS Grid/Flexbox** — Responsive layout with dark theme
- **Chart rendering** — Canvas-based charts drawn programmatically (no Chart.js)
- **Auto-refresh** — Tabs poll their data endpoints every 5-30 seconds
- **WebSocket integration** — Real-time event overlay on all tabs
- **Connection indicator** — Top-right status showing API connectivity
- **Glassmorphism UI** — backdrop-filter blur effects, gradient borders, card shadows
