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
// BetBR Sports Engine — format transformers
// Supports both:
//   - api-football.com (ApiFixtureItem) — kept for standings/scorers
//   - RapidAPI free-api-live-football-data (RapidLiveMatch) — live matches
// ---------------------------------------------------------------------------
import type {
  ApiFixtureItem,
  ApiStandingItem,
  ApiScorerItem,
  BetBRFixture,
  BetBRStanding,
  BetBRScorer,
  BetBREvent,
  BetBRStats,
  FixtureStatus,
} from './types';
import type { RapidLiveMatch } from './api-football';

// ---------------------------------------------------------------------------
// Status mapping
// ---------------------------------------------------------------------------
const STATUS_MAP: Record<string, FixtureStatus> = {
  // Live states
  '1H': 'LIVE', '2H': 'LIVE', 'ET': 'LIVE', 'P': 'LIVE', 'BT': 'LIVE', 'LIVE': 'LIVE',
  // Half-time
  'HT': 'HT',
  // Finished states
  'FT': 'FINISHED', 'AET': 'FINISHED', 'PEN': 'FINISHED',
  // Upcoming
  'NS': 'UPCOMING', 'TBD': 'UPCOMING',
  // Other
  'PST': 'POSTPONED', 'CANC': 'POSTPONED', 'ABD': 'POSTPONED',
  'AWD': 'FINISHED', 'WO': 'FINISHED',
};

function mapStatus(short: string): FixtureStatus {
  return STATUS_MAP[short] ?? 'UPCOMING';
}

// ---------------------------------------------------------------------------
// Fixture transformer
// ---------------------------------------------------------------------------
export function transformFixture(item: ApiFixtureItem): BetBRFixture {
  const status = mapStatus(item.fixture.status.short);

  const events: BetBREvent[] = (item.events ?? []).map(ev => {
    const isHome = ev.team.name === item.teams.home.name;
    let evType: BetBREvent['type'] = 'Substitution';
    if (ev.type === 'Goal') evType = 'Goal';
    else if (ev.type === 'Card') evType = 'Card';
    else if (ev.type === 'Var') evType = 'Var';

    return {
      minute: ev.time.elapsed,
      type: evType,
      team: isHome ? 'home' : 'away',
      player: ev.player.name,
      detail: ev.detail,
    };
  });

  let stats: BetBRStats | undefined;
  if (item.statistics && item.statistics.length >= 2) {
    const homeSt = item.statistics.find(s => s.team.name === item.teams.home.name)?.statistics ?? [];
    const awaySt = item.statistics.find(s => s.team.name === item.teams.away.name)?.statistics ?? [];

    const getStat = (arr: Array<{ type: string; value: number | string | null }>, type: string): number => {
      const found = arr.find(s => s.type === type);
      if (!found || found.value == null) return 0;
      if (typeof found.value === 'string') {
        // possession comes as "45%" — strip %
        return parseFloat(found.value.replace('%', '')) || 0;
      }
      return found.value as number;
    };

    stats = {
      possession: {
        home: getStat(homeSt, 'Ball Possession'),
        away: getStat(awaySt, 'Ball Possession'),
      },
      shots: {
        home: getStat(homeSt, 'Total Shots'),
        away: getStat(awaySt, 'Total Shots'),
      },
      shotsOnTarget: {
        home: getStat(homeSt, 'Shots on Goal'),
        away: getStat(awaySt, 'Shots on Goal'),
      },
      corners: {
        home: getStat(homeSt, 'Corner Kicks'),
        away: getStat(awaySt, 'Corner Kicks'),
      },
      fouls: {
        home: getStat(homeSt, 'Fouls'),
        away: getStat(awaySt, 'Fouls'),
      },
    };
  }

  return {
    id: item.fixture.id,
    league: item.league.name,
    leagueId: item.league.id,
    leagueLogo: item.league.logo,
    leagueCountry: item.league.country,
    home: {
      name: item.teams.home.name,
      logo: item.teams.home.logo,
      score: item.goals.home,
    },
    away: {
      name: item.teams.away.name,
      logo: item.teams.away.logo,
      score: item.goals.away,
    },
    status,
    minute: item.fixture.status.elapsed ?? null,
    date: item.fixture.date,
    venue: [item.fixture.venue.name, item.fixture.venue.city].filter(Boolean).join(', ') || undefined,
    // Odds are fetched separately; default null — the worker enriches after transform
    odds: null,
    events: events.length > 0 ? events : undefined,
    stats: stats ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Standings transformer
// ---------------------------------------------------------------------------
export function transformStanding(item: ApiStandingItem, rank: number): BetBRStanding {
  return {
    rank: item.rank ?? rank + 1,
    team: item.team.name,
    teamLogo: item.team.logo,
    played: item.all.played,
    won: item.all.win,
    drawn: item.all.draw,
    lost: item.all.lose,
    goalsFor: item.all.goals.for,
    goalsAgainst: item.all.goals.against,
    goalDifference: item.goalsDiff,
    points: item.points,
    form: item.form ?? '',
  };
}

// ---------------------------------------------------------------------------
// Top-scorers transformer
// ---------------------------------------------------------------------------
export function transformScorer(item: ApiScorerItem, rank: number): BetBRScorer {
  const stat = item.statistics[0];
  return {
    rank: rank + 1,
    player: item.player.name,
    team: stat?.team.name ?? '',
    teamLogo: stat?.team.logo ?? '',
    goals: stat?.goals.total ?? 0,
    assists: stat?.goals.assists ?? 0,
    photo: item.player.photo,
  };
}

// ---------------------------------------------------------------------------
// RapidAPI live fixture transformer
// RapidAPI response shape:
//   { id, leagueId, leagueName, leagueLogo, leagueCountry, time,
//     home: {id, name, logo, score}, away: {id, name, logo, score},
//     status: { type, scoreStr, reason: {short, long} }, elapsed }
// ---------------------------------------------------------------------------

const RAPID_STATUS_MAP: Record<string, FixtureStatus> = {
  // in-progress states
  'inprogress': 'LIVE',
  'live': 'LIVE',
  '1h': 'LIVE',
  '2h': 'LIVE',
  // half-time
  'halftime': 'HT',
  'ht': 'HT',
  // finished
  'finished': 'FINISHED',
  'fulltime': 'FINISHED',
  'ft': 'FINISHED',
  'aet': 'FINISHED',
  'pen': 'FINISHED',
  // upcoming
  'notstarted': 'UPCOMING',
  'ns': 'UPCOMING',
  'tbd': 'UPCOMING',
  // postponed / cancelled
  'postponed': 'POSTPONED',
  'cancelled': 'POSTPONED',
  'abandoned': 'POSTPONED',
};

function mapRapidStatus(statusType?: string): FixtureStatus {
  if (!statusType) return 'UPCOMING';
  return RAPID_STATUS_MAP[statusType.toLowerCase()] ?? 'UPCOMING';
}

export function transformRapidFixture(item: RapidLiveMatch): BetBRFixture {
  const status = mapRapidStatus(item.status?.type);

  return {
    id: item.id,
    league: item.leagueName ?? 'Unknown League',
    leagueId: item.leagueId ?? 0,
    leagueLogo: item.leagueLogo ?? '',
    leagueCountry: item.leagueCountry ?? '',
    home: {
      name: item.home?.name ?? 'Home',
      logo: item.home?.logo ?? '',
      score: item.home?.score ?? null,
    },
    away: {
      name: item.away?.name ?? 'Away',
      logo: item.away?.logo ?? '',
      score: item.away?.score ?? null,
    },
    status,
    minute: item.elapsed ?? null,
    date: item.time ?? new Date().toISOString(),
    venue: undefined,
    odds: null,
    events: undefined,
    stats: undefined,
  };
}
