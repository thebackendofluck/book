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
// BetBR Sports Engine — shared types
// ---------------------------------------------------------------------------

/** Environment bindings injected by the Workers runtime */
export interface Env {
  SPORTS_DATA: KVNamespace;
  RAPIDAPI_KEY?: string;
  // API-Football v3 key (separate subscription for upcoming, standings, scorers)
  // Set via: wrangler secret put API_FOOTBALL_KEY
  // Free tier: 100 req/day at https://rapidapi.com/api-sports/api/api-football
  API_FOOTBALL_KEY?: string;
  API_FOOTBALL_BASE: string;
  DEFAULT_LEAGUE_IDS: string;
  // Shared secret for /api/admin/refresh — set via: wrangler secret put REFRESH_TOKEN
  REFRESH_TOKEN?: string;
}

// ---------------------------------------------------------------------------
// Internal / BetBR wire format
// ---------------------------------------------------------------------------

export interface BetBRTeam {
  name: string;
  logo: string;
  score: number | null;
}

export interface BetBROdds {
  home: number;
  draw: number;
  away: number;
}

export interface BetBREvent {
  minute: number;
  type: 'Goal' | 'Card' | 'Substitution' | 'Var';
  team: 'home' | 'away';
  player: string;
  detail?: string;
}

export interface BetBRStats {
  possession: { home: number; away: number };
  shots: { home: number; away: number };
  shotsOnTarget: { home: number; away: number };
  corners: { home: number; away: number };
  fouls: { home: number; away: number };
}

export type FixtureStatus = 'LIVE' | 'UPCOMING' | 'FINISHED' | 'HT' | 'POSTPONED';

export interface BetBRFixture {
  id: number;
  league: string;
  leagueId: number;
  leagueLogo: string;
  leagueCountry: string;
  home: BetBRTeam;
  away: BetBRTeam;
  status: FixtureStatus;
  minute: number | null;
  date: string;          // ISO-8601
  venue?: string;
  odds: BetBROdds | null;
  events?: BetBREvent[];
  stats?: BetBRStats;
}

export interface BetBRStanding {
  rank: number;
  team: string;
  teamLogo: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goalsFor: number;
  goalsAgainst: number;
  goalDifference: number;
  points: number;
  form: string;
}

export interface BetBRScorer {
  rank: number;
  player: string;
  team: string;
  teamLogo: string;
  goals: number;
  assists: number;
  photo: string;
}

// ---------------------------------------------------------------------------
// KV cache envelope types
// ---------------------------------------------------------------------------

export interface LiveFixturesCache {
  fixtures: BetBRFixture[];
  updated_at: string | null;
  source: 'api' | 'fallback';
}

export interface UpcomingFixturesCache {
  fixtures: BetBRFixture[];
  updated_at: string | null;
  source: 'api' | 'fallback';
}

export interface StandingsCache {
  standings: BetBRStanding[];
  leagueId: number;
  season: number;
  updated_at: string | null;
  source: 'api' | 'fallback';
}

export interface ScorersCache {
  scorers: BetBRScorer[];
  leagueId: number;
  season: number;
  updated_at: string | null;
  source: 'api' | 'fallback';
}

// ---------------------------------------------------------------------------
// Odds types (Feature 2)
// ---------------------------------------------------------------------------

export interface OddsEntry {
  fixtureId: number;
  bookmaker: string;
  home: number;
  draw: number;
  away: number;
  overUnder25: { over: number; under: number } | null;
  btts: { yes: number; no: number } | null;
  updatedAt: string;
}

export interface OddsCache {
  odds: OddsEntry[];
  updated_at: string | null;
  source: 'api' | 'fallback';
}

// ---------------------------------------------------------------------------
// Live statistics types (Feature 3)
// ---------------------------------------------------------------------------

export interface LiveStatsCache {
  stats: Record<number, { stats: BetBRStats; events: BetBREvent[] }>;
  updated_at: string | null;
  source: 'api' | 'fallback';
}

// ---------------------------------------------------------------------------
// Predictions types (Feature 4)
// ---------------------------------------------------------------------------

export interface PredictionEntry {
  fixtureId: number;
  winner: { name: string; comment: string } | null;
  advice: string;
  percent: { home: string; draw: string; away: string };
  goalsHome: string;
  goalsAway: string;
}

export interface PredictionsCache {
  predictions: PredictionEntry[];
  updated_at: string | null;
  source: 'api' | 'fallback';
}

// ---------------------------------------------------------------------------
// API-Football raw response types (subset we actually use)
// ---------------------------------------------------------------------------

export interface ApiFootballResponse<T> {
  get: string;
  parameters: Record<string, string>;
  errors: string[] | Record<string, string>;
  results: number;
  paging: { current: number; total: number };
  response: T[];
}

export interface ApiFixtureItem {
  fixture: {
    id: number;
    date: string;
    venue: { name: string | null; city: string | null };
    status: { long: string; short: string; elapsed: number | null };
  };
  league: {
    id: number;
    name: string;
    country: string;
    logo: string;
  };
  teams: {
    home: { id: number; name: string; logo: string; winner: boolean | null };
    away: { id: number; name: string; logo: string; winner: boolean | null };
  };
  goals: { home: number | null; away: number | null };
  score: {
    halftime: { home: number | null; away: number | null };
    fulltime: { home: number | null; away: number | null };
    extratime: { home: number | null; away: number | null };
    penalty: { home: number | null; away: number | null };
  };
  events?: Array<{
    time: { elapsed: number; extra: number | null };
    team: { id: number; name: string };
    player: { id: number; name: string };
    type: string;
    detail: string;
  }>;
  statistics?: Array<{
    team: { id: number; name: string };
    statistics: Array<{ type: string; value: number | string | null }>;
  }>;
}

export interface ApiStandingItem {
  rank: number;
  team: { id: number; name: string; logo: string };
  points: number;
  goalsDiff: number;
  group: string;
  form: string;
  status: string;
  description: string;
  all: { played: number; win: number; draw: number; lose: number; goals: { for: number; against: number } };
}

export interface ApiScorerItem {
  player: { id: number; name: string; photo: string };
  statistics: Array<{
    team: { id: number; name: string; logo: string };
    goals: { total: number | null; assists: number | null };
  }>;
}

// ---------------------------------------------------------------------------
// API-Football odds raw response (subset)
// ---------------------------------------------------------------------------

export interface ApiOddsBookmakerValue {
  value: string;
  odd: string;
}

export interface ApiOddsBet {
  id: number;
  name: string;
  values: ApiOddsBookmakerValue[];
}

export interface ApiOddsBookmaker {
  id: number;
  name: string;
  bets: ApiOddsBet[];
}

export interface ApiOddsItem {
  fixture: { id: number };
  bookmakers: ApiOddsBookmaker[];
}

// ---------------------------------------------------------------------------
// API-Football predictions raw response (subset)
// ---------------------------------------------------------------------------

export interface ApiPredictionItem {
  predictions: {
    winner: { id: number | null; name: string | null; comment: string | null } | null;
    win_or_draw: boolean;
    under_over: string | null;
    goals: { home: string | null; away: string | null };
    advice: string | null;
    percent: { home: string; draw: string; away: string };
  };
  fixture: { id: number };
}
