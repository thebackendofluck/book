# betbr-sports-engine

Cloudflare Worker that fetches live sports data from API-Football and caches it in KV, serving it to the bet-brazil frontend via a simple REST API.

## Architecture

```
Cron (*/30 min) --> Worker --> API-Football v3 --> KV store
                                     |
Frontend (bet-brazil) --> Worker GET /api/* --> KV read --> JSON response
```

When the API key is absent or quota is exhausted, the worker returns pre-seeded fallback data so the frontend always has something to display.

## Project structure

```
sports-engine/
  wrangler.toml         # Worker config, cron trigger, KV binding
  src/
    index.ts            # Entry point: fetch handler + scheduled handler
    api-football.ts     # API-Football v3 client (live, upcoming, standings, scorers)
    transform.ts        # Raw API response -> BetBR wire format
    types.ts            # Shared TypeScript interfaces
    fallback.ts         # Hardcoded fallback fixtures / standings / scorers
  scripts/
    seed-fallback.js    # Seeds KV with fallback data (no build needed)
  package.json
  tsconfig.json
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Engine info and endpoint list |
| GET | `/api/fixtures/live` | Currently playing matches |
| GET | `/api/fixtures/upcoming` | Matches in the next 48 hours |
| GET | `/api/standings` | Brasileirão Série A standings |
| GET | `/api/scorers` | Brasileirão top scorers |
| GET | `/api/health` | Health check with timestamp |

All responses are JSON with `Content-Type: application/json` and permissive CORS headers (`Access-Control-Allow-Origin: *`). Narrow the CORS origin to your production domain before going live.

## Tracked leagues

| ID | Competition |
|----|-------------|
| 71 | Brasileirão Série A |
| 73 | Copa do Brasil |
| 13 | Copa Libertadores |
| 2  | Champions League |
| 39 | Premier League |
| 140 | La Liga |
| 135 | Serie A (Italy) |

## Getting an API-Football key

1. Go to https://rapidapi.com/api-sports/api/api-football (or https://www.api-football.com)
2. Sign up for the free plan (100 requests/day)
3. Copy your `X-RapidAPI-Key`

The cron runs every 30 minutes, that is 48 ticks/day. With multiple endpoint calls per cron tick (live + upcoming), the free tier is consumed quickly. To stay within budget:
- Live fixtures: 1 call (`/fixtures?live=all` covers all leagues at once)
- Upcoming fixtures: 2 calls (today + tomorrow)
- Standings: 1 call (runs but skips update if quota is low)
- Scorers: 1 call (same guard)

Total per cron tick: 5 calls x 48 ticks = 240 calls/day on free tier (upgrade to Basic at ~$10/month for 7 500 calls/day).

To reduce calls further, comment out `updateStandings` and `updateTopScorers` in `src/index.ts`; those data change at most once per matchday.

## Deployment

### 1. Install dependencies

```bash
npm install
```

### 2. Create the KV namespace

```bash
wrangler kv namespace create SPORTS_DATA
# Also create a preview namespace for local dev:
wrangler kv namespace create SPORTS_DATA --preview
```

Copy the returned `id` values into `wrangler.toml`:
```toml
[[kv_namespaces]]
binding = "SPORTS_DATA"
id = "<production-id>"
preview_id = "<preview-id>"
```

### 3. Seed fallback data

This populates KV so the frontend gets a valid response immediately, before the first cron fires:

```bash
node scripts/seed-fallback.js --namespace-id=<production-id>
```

### 4. Set the API key secret (optional)

```bash
wrangler secret put API_FOOTBALL_KEY
# Paste your key when prompted
```

If you skip this step, the worker runs in fallback-only mode indefinitely.

### 5. Deploy

```bash
wrangler deploy
```

Your worker URL will be:
```
https://betbr-sports-engine.<your-subdomain>.workers.dev
```

### 6. Connect the frontend

In `landing_page/index.html`, add this line **before** the main `<script>` block (or anywhere before the end of `<body>`):

```html
<script>
  window.BETBR_SPORTS_ENGINE = 'https://betbr-sports-engine.<your-subdomain>.workers.dev';
</script>
```

The `SportsEngineClient` integration block already present in `index.html` picks up this variable and starts polling every 60 seconds. When the engine is unreachable, it silently falls back to the built-in simulation — no user-visible error.

## Local development

```bash
# Start local dev server (uses preview KV namespace)
wrangler dev

# Test the endpoints
curl http://localhost:8787/api/fixtures/live | jq .
curl http://localhost:8787/api/fixtures/upcoming | jq .
curl http://localhost:8787/api/standings | jq .
curl http://localhost:8787/api/scorers | jq .

# Trigger the cron manually
curl -X POST http://localhost:8787/__scheduled?cron=%2A%2F15+%2A+%2A+%2A+%2A
```

## KV key structure

| Key | TTL | Description |
|-----|-----|-------------|
| `live_fixtures` | 900 s (15 min) | LiveFixturesCache JSON |
| `upcoming_fixtures` | 1800 s (30 min) | UpcomingFixturesCache JSON |
| `standings` | 21600 s (6 h) | StandingsCache JSON (Brasileirão) |
| `top_scorers` | 21600 s (6 h) | ScorersCache JSON (Brasileirão) |

## Fallback behaviour

The fallback cascade works as follows:

1. Cron runs → `fetchLiveFixtures()` calls API-Football
2. If API returns data → store in KV with TTL
3. If API fails (no key / quota / network) → check if a KV value already exists
4. If KV is empty → write the built-in `FALLBACK_LIVE` constant to KV
5. On HTTP GET → `getFromKV()` reads from KV; if KV misses (TTL expired) → returns the in-memory `FALLBACK_*` constant as a last resort

This means the frontend never receives an empty response.

## Cloudflare KV free tier limits (as of 2025)

| Metric | Free limit |
|--------|-----------|
| Reads | 100 000 / day |
| Writes | 1 000 / day |
| Storage | 1 GB |

With 4 keys written every 30 minutes: 4 x 48 = 192 writes/day, well within the free tier.

See `reference_cloudflare_kv_limits.md` in the project memory for caching strategy details.

## Type-checking

```bash
npm run type-check
```

No build step is required — Wrangler handles TypeScript compilation internally on `wrangler deploy` and `wrangler dev`.
