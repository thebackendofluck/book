// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// ---------------------------------------------------------------------------
// BetBR Sports Engine — dual API client
//   1. RapidAPI free-api-live-football-data → live matches
//   2. API-Football v3 (api-sports.io via RapidAPI) → upcoming, standings, scorers
// ---------------------------------------------------------------------------
import type {
  Env,
  BetBRFixture,
  BetBRStats,
  BetBREvent,
  LiveFixturesCache,
  UpcomingFixturesCache,
  StandingsCache,
  ScorersCache,
  OddsCache,
  OddsEntry,
  LiveStatsCache,
  PredictionsCache,
  PredictionEntry,
  ApiFootballResponse,
  ApiFixtureItem,
  ApiStandingItem,
  ApiScorerItem,
  ApiOddsItem,
  ApiOddsBookmaker,
  ApiPredictionItem,
} from './types';
import { transformRapidFixture, transformFixture, transformStanding, transformScorer } from './transform';

// ── Live matches API ────────────────────────────────────────────────────────
const RAPID_LIVE_HOST = 'free-api-live-football-data.p.rapidapi.com';
const RAPID_LIVE_BASE = 'https://free-api-live-football-data.p.rapidapi.com';

// ── API-Football v3 (upcoming, standings, scorers) ──────────────────────────
const APIFOOTBALL_HOST = 'v3.football.api-sports.io';
const APIFOOTBALL_BASE = 'https://v3.football.api-sports.io';

// Tracked league IDs
// Brasileirão A (71), Serie B (72), Copa BR (73), Libertadores (13), Sul-Americana (11),
// UCL (2), EPL (39), La Liga (140), Serie A IT (135), Paulista A1 (475), Carioca 1 (624), Copa do Nordeste (612)
const LEAGUE_IDS = [71, 72, 73, 13, 11, 2, 39, 140, 135, 475, 624, 612];

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Low-level fetch helpers
// ---------------------------------------------------------------------------
async function rapidFetch<T>(apiKey: string, host: string, base: string, path: string): Promise<T | null> {
  const url = `${base}${path}`;
  // Validate the constructed URL to prevent incomplete/unsafe URL schemes.
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:') {
      console.error(`[api] Rejected non-HTTPS URL: ${url}`);
      return null;
    }
  } catch {
    console.error(`[api] Invalid URL: ${url}`);
    return null;
  }
  // api-sports.io direct host expects `x-apisports-key`; RapidAPI proxy hosts expect `x-rapidapi-*`.
  const isApiSportsDirect = host.endsWith('api-sports.io');
  const headers: Record<string, string> = isApiSportsDirect
    ? { 'x-apisports-key': apiKey, 'Content-Type': 'application/json' }
    : { 'x-rapidapi-host': host, 'x-rapidapi-key': apiKey, 'Content-Type': 'application/json' };
  try {
    const resp = await fetch(url, { headers });
    if (!resp.ok) {
      console.error(`[api] HTTP ${resp.status} for ${host}${path}`);
      return null;
    }
    const body = await resp.json() as T & { errors?: unknown };
    // api-sports.io returns 200 with a non-empty `errors` object on auth/subscription failures.
    if (isApiSportsDirect && body && typeof body === 'object' && 'errors' in body) {
      const errs = (body as { errors?: unknown }).errors;
      if (errs && typeof errs === 'object' && Object.keys(errs as object).length > 0) {
        console.error(`[api] api-sports.io errors for ${path}:`, JSON.stringify(errs));
        return null;
      }
    }
    return body;
  } catch (err) {
    console.error(`[api] fetch error for ${host}${path}:`, err);
    return null;
  }
}

// ---------------------------------------------------------------------------
// 1. Live fixtures (free-api-live-football-data)
// ---------------------------------------------------------------------------
export async function fetchLiveFixtures(env: Env): Promise<LiveFixturesCache | null> {
  if (!env.RAPIDAPI_KEY) {
    console.warn('[api] No RAPIDAPI_KEY — skipping live fetch');
    return null;
  }

  const data = await rapidFetch<{ response?: { live?: unknown[] } }>(
    env.RAPIDAPI_KEY, RAPID_LIVE_HOST, RAPID_LIVE_BASE,
    '/football-current-live',
  );

  if (!data?.response?.live || !Array.isArray(data.response.live)) {
    console.warn('[api] No live matches in response');
    return null;
  }

  const fixtures: BetBRFixture[] = data.response.live.map((item: unknown) =>
    transformRapidFixture(item as RapidLiveMatch),
  );

  return { fixtures, updated_at: new Date().toISOString(), source: 'api' };
}

// ---------------------------------------------------------------------------
// 2. Upcoming fixtures (API-Football v3 — next 7 days per league)
// ---------------------------------------------------------------------------
export async function fetchUpcomingFixtures(env: Env): Promise<UpcomingFixturesCache | null> {
  // API-Football v3 requires its own subscription key (separate from the free live API)
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  if (!apiKey) {
    console.warn('[api] No API key — skipping upcoming fetch');
    return null;
  }

  const today = new Date();
  const futureDate = new Date(today);
  futureDate.setDate(futureDate.getDate() + 7); // Next 7 days
  const fromDate = today.toISOString().slice(0, 10);
  const toDate = futureDate.toISOString().slice(0, 10);
  const year = today.getFullYear();
  // European leagues (UCL 2, EPL 39, La Liga 140, Serie A 135) span Aug→May → season = start year.
  // Brazilian/S-American calendar-year leagues → season = current year.
  const CALENDAR_YEAR_LEAGUES = new Set([71, 72, 73, 13, 11, 475, 624, 612]);
  const seasonFor = (leagueId: number): number =>
    CALENDAR_YEAR_LEAGUES.has(leagueId) ? year : (today.getMonth() >= 6 ? year : year - 1);

  const allFixtures: BetBRFixture[] = [];

  // Fetch upcoming for each tracked league (1 call per league).
  // api-sports.io free tier rate-limits per minute — throttle 1.2s between calls.
  for (let i = 0; i < LEAGUE_IDS.length; i++) {
    const leagueId = LEAGUE_IDS[i];
    try {
      const season = seasonFor(leagueId);
      const data = await rapidFetch<ApiFootballResponse<ApiFixtureItem>>(
        apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
        `/fixtures?league=${leagueId}&season=${season}&from=${fromDate}&to=${toDate}&status=NS`,
      );

      if (data?.response) {
        const fixtures = data.response.map(transformFixture);
        allFixtures.push(...fixtures);
        console.log(`[api] league ${leagueId}: ${data.response.length} upcoming fixtures`);
      }
    } catch (err) {
      console.warn(`[api] league ${leagueId} fetch failed:`, err);
    }
    if (i < LEAGUE_IDS.length - 1) await sleep(1200);
  }

  if (allFixtures.length === 0) {
    console.warn('[api] No upcoming fixtures from any league — will use fallback');
    return null;
  }

  // Sort by date
  allFixtures.sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

  return { fixtures: allFixtures, updated_at: new Date().toISOString(), source: 'api' };
}

// ---------------------------------------------------------------------------
// 3. Standings (API-Football v3 — Brasileirão A + Serie B)
// ---------------------------------------------------------------------------
export async function fetchStandings(env: Env): Promise<StandingsCache | null> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  if (!apiKey) return null;

  // Brasileirão (league 71) is calendar-year — always use current year.
  const season = new Date().getFullYear();

  const data = await rapidFetch<ApiFootballResponse<{ league: { standings: ApiStandingItem[][] } }>>(
    apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
    `/standings?league=71&season=${season}`,
  );

  if (!data?.response?.[0]?.league?.standings?.[0]) {
    console.warn('[api] No standings data');
    return null;
  }

  const standings = data.response[0].league.standings[0].map((item, i) => transformStanding(item, i));

  return { standings, leagueId: 71, season, updated_at: new Date().toISOString(), source: 'api' };
}

export async function fetchSerieBStandings(env: Env): Promise<StandingsCache | null> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  if (!apiKey) return null;

  const season = new Date().getFullYear();

  const data = await rapidFetch<ApiFootballResponse<{ league: { standings: ApiStandingItem[][] } }>>(
    apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
    `/standings?league=72&season=${season}`,
  );

  if (!data?.response?.[0]?.league?.standings?.[0]) {
    console.warn('[api] No Serie B standings data');
    return null;
  }

  const standings = data.response[0].league.standings[0].map((item, i) => transformStanding(item, i));

  return { standings, leagueId: 72, season, updated_at: new Date().toISOString(), source: 'api' };
}

// ---------------------------------------------------------------------------
// 4. Top Scorers (API-Football v3 — Brasileirão A + Serie B)
// ---------------------------------------------------------------------------
export async function fetchTopScorers(env: Env): Promise<ScorersCache | null> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  if (!apiKey) return null;

  // Brasileirão (league 71) is calendar-year — always use current year.
  const season = new Date().getFullYear();

  const data = await rapidFetch<ApiFootballResponse<ApiScorerItem>>(
    apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
    `/players/topscorers?league=71&season=${season}`,
  );

  if (!data?.response) {
    console.warn('[api] No scorers data');
    return null;
  }

  const scorers = data.response.slice(0, 20).map((item, i) => transformScorer(item, i + 1));

  return { scorers, leagueId: 71, season, updated_at: new Date().toISOString(), source: 'api' };
}

export async function fetchSerieBTopScorers(env: Env): Promise<ScorersCache | null> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  if (!apiKey) return null;

  const season = new Date().getFullYear();

  const data = await rapidFetch<ApiFootballResponse<ApiScorerItem>>(
    apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
    `/players/topscorers?league=72&season=${season}`,
  );

  if (!data?.response) {
    console.warn('[api] No Serie B scorers data');
    return null;
  }

  const scorers = data.response.slice(0, 20).map((item, i) => transformScorer(item, i + 1));

  return { scorers, leagueId: 72, season, updated_at: new Date().toISOString(), source: 'api' };
}

// ---------------------------------------------------------------------------
// 5. Odds (Feature 2) — up to 20 upcoming fixture IDs per cron run
// ---------------------------------------------------------------------------

/**
 * Extract a numeric odd value from a bookmaker bet values array.
 * Returns null if the label is not found or the odd is non-numeric.
 */
function extractOdd(values: Array<{ value: string; odd: string }>, label: string): number | null {
  const entry = values.find((v) => v.value.toLowerCase() === label.toLowerCase());
  if (!entry) return null;
  const n = parseFloat(entry.odd);
  return isNaN(n) ? null : n;
}

/**
 * Pick the preferred bookmaker from an odds response.
 * Prefers Bet365 (id=6); falls back to the first available bookmaker.
 */
function pickBookmaker(bookmakers: ApiOddsBookmaker[]): ApiOddsBookmaker | null {
  if (!bookmakers || bookmakers.length === 0) return null;
  return bookmakers.find((b) => b.id === 6) ?? bookmakers[0];
}

export async function fetchOdds(env: Env, fixtureIds: number[]): Promise<OddsCache> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  const empty: OddsCache = { odds: [], updated_at: null, source: 'fallback' };
  if (!apiKey || fixtureIds.length === 0) return empty;

  // Cap at 20 fixtures per cron run to stay within budget
  const ids = fixtureIds.slice(0, 20);
  const oddsEntries: OddsEntry[] = [];
  const now = new Date().toISOString();

  for (let i = 0; i < ids.length; i++) {
    const fixtureId = ids[i];
    try {
      const data = await rapidFetch<ApiFootballResponse<ApiOddsItem>>(
        apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
        `/odds?fixture=${fixtureId}`,
      );

      if (data?.response?.[0]) {
        const item = data.response[0];
        const bm = pickBookmaker(item.bookmakers);

        if (bm) {
          // Match winner bet (id=1 in API-Football)
          const matchWinner = bm.bets.find((b) => b.id === 1 || b.name === 'Match Winner');
          const home = matchWinner ? extractOdd(matchWinner.values, 'Home') : null;
          const draw = matchWinner ? extractOdd(matchWinner.values, 'Draw') : null;
          const away = matchWinner ? extractOdd(matchWinner.values, 'Away') : null;

          // Goals Over/Under 2.5 (bet id=5)
          const ouBet = bm.bets.find((b) => b.id === 5 || b.name === 'Goals Over/Under');
          let overUnder25: OddsEntry['overUnder25'] = null;
          if (ouBet) {
            const over = extractOdd(ouBet.values, 'Over 2.5');
            const under = extractOdd(ouBet.values, 'Under 2.5');
            if (over !== null && under !== null) overUnder25 = { over, under };
          }

          // Both Teams Score (bet id=8)
          const bttsBet = bm.bets.find((b) => b.id === 8 || b.name === 'Both Teams Score');
          let btts: OddsEntry['btts'] = null;
          if (bttsBet) {
            const yes = extractOdd(bttsBet.values, 'Yes');
            const no = extractOdd(bttsBet.values, 'No');
            if (yes !== null && no !== null) btts = { yes, no };
          }

          if (home !== null && draw !== null && away !== null) {
            oddsEntries.push({
              fixtureId,
              bookmaker: bm.name,
              home,
              draw,
              away,
              overUnder25,
              btts,
              updatedAt: now,
            });
          }
        }
      }
    } catch (err) {
      console.warn(`[api] odds fetch failed for fixture ${fixtureId}:`, err);
    }

    if (i < ids.length - 1) await sleep(1200);
  }

  if (oddsEntries.length === 0) {
    console.warn('[api] No odds data retrieved');
    return empty;
  }

  return { odds: oddsEntries, updated_at: now, source: 'api' };
}

// ---------------------------------------------------------------------------
// 6. Live fixture statistics (Feature 3) — supplementary V3 data
// ---------------------------------------------------------------------------

/**
 * Parse statistics array from API-Football fixture detail into BetBRStats.
 * The statistics array contains two objects (one per team), each with a
 * `statistics` array of { type, value } pairs.
 */
function parseApiStats(
  statistics: ApiFixtureItem['statistics'],
): BetBRStats | null {
  if (!statistics || statistics.length < 2) return null;

  const getVal = (teamIdx: number, type: string): number => {
    const teamStats = statistics[teamIdx]?.statistics ?? [];
    const entry = teamStats.find((s) => s.type === type);
    if (!entry || entry.value === null) return 0;
    // Some values arrive as "45%" strings
    const raw = String(entry.value).replace('%', '');
    const n = parseFloat(raw);
    return isNaN(n) ? 0 : n;
  };

  return {
    possession: { home: getVal(0, 'Ball Possession'), away: getVal(1, 'Ball Possession') },
    shots: { home: getVal(0, 'Total Shots'), away: getVal(1, 'Total Shots') },
    shotsOnTarget: { home: getVal(0, 'Shots on Goal'), away: getVal(1, 'Shots on Goal') },
    corners: { home: getVal(0, 'Corner Kicks'), away: getVal(1, 'Corner Kicks') },
    fouls: { home: getVal(0, 'Fouls'), away: getVal(1, 'Fouls') },
  };
}

/**
 * Parse events array from API-Football fixture detail into BetBREvent[].
 */
function parseApiEvents(
  rawEvents: ApiFixtureItem['events'],
  homeTeamId: number,
): BetBREvent[] {
  if (!rawEvents) return [];
  return rawEvents.map((e) => {
    const typeRaw = e.type?.toLowerCase() ?? '';
    let type: BetBREvent['type'] = 'Goal';
    if (typeRaw === 'card') type = 'Card';
    else if (typeRaw === 'subst') type = 'Substitution';
    else if (typeRaw === 'var') type = 'Var';
    return {
      minute: e.time?.elapsed ?? 0,
      type,
      team: e.team?.id === homeTeamId ? 'home' : 'away',
      player: e.player?.name ?? '',
      detail: e.detail ?? undefined,
    };
  });
}

export async function fetchLiveFixturesV3(env: Env): Promise<LiveStatsCache> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  const empty: LiveStatsCache = { stats: {}, updated_at: null, source: 'fallback' };
  if (!apiKey) return empty;

  // Step 1: fetch all currently live matches (1 call)
  const liveData = await rapidFetch<ApiFootballResponse<ApiFixtureItem>>(
    apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
    '/fixtures?live=all',
  );

  if (!liveData?.response || liveData.response.length === 0) {
    console.warn('[api] No live fixtures from V3');
    return empty;
  }

  console.log(`[api] V3 live: ${liveData.response.length} live fixtures`);

  // Step 2: fetch detail (stats + events) for the first 5 live fixtures
  const liveFixtures = liveData.response.slice(0, 5);
  const statsMap: LiveStatsCache['stats'] = {};

  for (let i = 0; i < liveFixtures.length; i++) {
    const fixture = liveFixtures[i];
    const fixtureId = fixture.fixture.id;
    const homeTeamId = fixture.teams.home.id;

    await sleep(1200);

    try {
      const detail = await rapidFetch<ApiFootballResponse<ApiFixtureItem>>(
        apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
        `/fixtures?id=${fixtureId}`,
      );

      if (detail?.response?.[0]) {
        const d = detail.response[0];
        const stats = parseApiStats(d.statistics);
        const events = parseApiEvents(d.events, homeTeamId);

        if (stats) {
          statsMap[fixtureId] = { stats, events };
        }
      }
    } catch (err) {
      console.warn(`[api] live stats fetch failed for fixture ${fixtureId}:`, err);
    }
  }

  if (Object.keys(statsMap).length === 0) {
    console.warn('[api] No live stats extracted from V3');
    return empty;
  }

  return { stats: statsMap, updated_at: new Date().toISOString(), source: 'api' };
}

// ---------------------------------------------------------------------------
// 7. Predictions (Feature 4) — up to 15 upcoming fixture IDs per cron run
// ---------------------------------------------------------------------------
export async function fetchPredictions(env: Env, fixtureIds: number[]): Promise<PredictionsCache> {
  const apiKey = env.API_FOOTBALL_KEY ?? env.RAPIDAPI_KEY;
  const empty: PredictionsCache = { predictions: [], updated_at: null, source: 'fallback' };
  if (!apiKey || fixtureIds.length === 0) return empty;

  // Cap at 15 fixtures per cron run
  const ids = fixtureIds.slice(0, 15);
  const predictionEntries: PredictionEntry[] = [];
  const now = new Date().toISOString();

  for (let i = 0; i < ids.length; i++) {
    const fixtureId = ids[i];
    try {
      const data = await rapidFetch<ApiFootballResponse<ApiPredictionItem>>(
        apiKey, APIFOOTBALL_HOST, APIFOOTBALL_BASE,
        `/predictions?fixture=${fixtureId}`,
      );

      if (data?.response?.[0]) {
        const item = data.response[0];
        const p = item.predictions;

        predictionEntries.push({
          fixtureId,
          winner: p.winner?.name
            ? { name: p.winner.name, comment: p.winner.comment ?? '' }
            : null,
          advice: p.advice ?? '',
          percent: {
            home: p.percent?.home ?? '0%',
            draw: p.percent?.draw ?? '0%',
            away: p.percent?.away ?? '0%',
          },
          goalsHome: p.goals?.home ?? '-',
          goalsAway: p.goals?.away ?? '-',
        });
      }
    } catch (err) {
      console.warn(`[api] predictions fetch failed for fixture ${fixtureId}:`, err);
    }

    if (i < ids.length - 1) await sleep(1200);
  }

  if (predictionEntries.length === 0) {
    console.warn('[api] No predictions data retrieved');
    return empty;
  }

  return { predictions: predictionEntries, updated_at: now, source: 'api' };
}

// ---------------------------------------------------------------------------
// RapidAPI live match shape (subset we actually use)
// ---------------------------------------------------------------------------
export interface RapidLiveMatch {
  id: number;
  leagueId?: number;
  leagueName?: string;
  leagueLogo?: string;
  leagueCountry?: string;
  time?: string;
  home?: {
    id?: number;
    name?: string;
    logo?: string;
    score?: number | null;
  };
  away?: {
    id?: number;
    name?: string;
    logo?: string;
    score?: number | null;
  };
  status?: {
    type?: string;       // e.g. "inprogress", "halftime", "finished"
    scoreStr?: string;   // e.g. "1 - 0"
    reason?: { short?: string; long?: string };
  };
  elapsed?: number | null;
}
