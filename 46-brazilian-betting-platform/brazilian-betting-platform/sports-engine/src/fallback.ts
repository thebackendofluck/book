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
// BetBR Sports Engine — hardcoded fallback data
// Used when API-Football is unavailable or quota is exhausted.
// Matches the same fixtures used in the landing_page simulation engine.
// ---------------------------------------------------------------------------
import type {
  LiveFixturesCache,
  UpcomingFixturesCache,
  StandingsCache,
  ScorersCache,
} from './types';

// ---------------------------------------------------------------------------
// Live fixtures fallback
// ---------------------------------------------------------------------------
// No live fallback fixtures — Brasileirão 2026 starts April 12.
// All fallback data is UPCOMING. When API-Football is unavailable,
// return an empty live list so the UI shows upcoming fixtures instead.
export const FALLBACK_LIVE: LiveFixturesCache = {
  source: 'fallback',
  updated_at: null,
  fixtures: [],
};

// ---------------------------------------------------------------------------
// Helper: build an ISO date string for a given April 2026 date + BRT time
// BRT = UTC-3, so "16:00 BRT" = "19:00 UTC"
// ---------------------------------------------------------------------------
function apr2026(day: number, hourBRT: number, minuteBRT = 0): string {
  const hourUTC = hourBRT + 3;
  return `2026-04-${String(day).padStart(2, '0')}T${String(hourUTC).padStart(2, '0')}:${String(minuteBRT).padStart(2, '0')}:00.000Z`;
}

export const FALLBACK_UPCOMING: UpcomingFixturesCache = {
  source: 'fallback',
  updated_at: null,
  fixtures: [
    // ── Champions League 2025/26 — Quartas de Final 1st Leg (07-08/04) ──────
    {
      id: 2001,
      league: 'UEFA Champions League — Quartas de Final',
      leagueId: 2,
      leagueLogo: 'https://media.api-sports.io/football/leagues/2.png',
      leagueCountry: 'World',
      home: {
        name: 'Real Madrid',
        logo: 'https://upload.wikimedia.org/wikipedia/en/5/56/Real_Madrid_CF.svg',
        score: null,
      },
      away: {
        name: 'Bayern Munich',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/1/1b/FC_Bayern_M%C3%BCnchen_logo_%282017%29.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(7, 16, 0), // TER 07/04 16:00 BRT
      venue: 'Estadio Santiago Bernabéu, Madrid',
      odds: { home: 1.85, draw: 3.40, away: 4.20 },
    },
    {
      id: 2002,
      league: 'UEFA Champions League — Quartas de Final',
      leagueId: 2,
      leagueLogo: 'https://media.api-sports.io/football/leagues/2.png',
      leagueCountry: 'World',
      home: {
        name: 'Barcelona',
        logo: 'https://upload.wikimedia.org/wikipedia/en/4/47/FC_Barcelona_%28crest%29.svg',
        score: null,
      },
      away: {
        name: 'Inter Milan',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/0/05/FC_Internazionale_Milano_2021.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(8, 16, 0), // QUA 08/04 16:00 BRT
      venue: 'Estadi Olímpic Lluís Companys, Barcelona',
      odds: { home: 1.90, draw: 3.40, away: 4.00 },
    },
    // ── Champions League 2025/26 — Quartas de Final 2nd Leg (14-15/04) ────
    {
      id: 2020,
      league: 'UEFA Champions League — Quartas de Final (Volta)',
      leagueId: 2,
      leagueLogo: 'https://media.api-sports.io/football/leagues/2.png',
      leagueCountry: 'World',
      home: {
        name: 'Bayern Munich',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/1/1b/FC_Bayern_M%C3%BCnchen_logo_%282017%29.svg',
        score: null,
      },
      away: {
        name: 'Real Madrid',
        logo: 'https://upload.wikimedia.org/wikipedia/en/5/56/Real_Madrid_CF.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(14, 16, 0), // TER 14/04 16:00 BRT
      venue: 'Allianz Arena, Munich',
      odds: { home: 2.10, draw: 3.30, away: 3.40 },
    },
    {
      id: 2021,
      league: 'UEFA Champions League — Quartas de Final (Volta)',
      leagueId: 2,
      leagueLogo: 'https://media.api-sports.io/football/leagues/2.png',
      leagueCountry: 'World',
      home: {
        name: 'Inter Milan',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/0/05/FC_Internazionale_Milano_2021.svg',
        score: null,
      },
      away: {
        name: 'Barcelona',
        logo: 'https://upload.wikimedia.org/wikipedia/en/4/47/FC_Barcelona_%28crest%29.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(15, 16, 0), // QUA 15/04 16:00 BRT
      venue: 'Stadio Giuseppe Meazza, Milan',
      odds: { home: 2.30, draw: 3.20, away: 3.00 },
    },
    // ── Champions League 2025/26 — Semifinal 1st Leg (28-29/04) ───────────
    {
      id: 2030,
      league: 'UEFA Champions League — Semifinal',
      leagueId: 2,
      leagueLogo: 'https://media.api-sports.io/football/leagues/2.png',
      leagueCountry: 'World',
      home: {
        name: 'Real Madrid',
        logo: 'https://upload.wikimedia.org/wikipedia/en/5/56/Real_Madrid_CF.svg',
        score: null,
      },
      away: {
        name: 'Barcelona',
        logo: 'https://upload.wikimedia.org/wikipedia/en/4/47/FC_Barcelona_%28crest%29.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(28, 16, 0), // TER 28/04 16:00 BRT
      venue: 'Estadio Santiago Bernabéu, Madrid',
      odds: { home: 1.95, draw: 3.30, away: 3.80 },
    },
    {
      id: 2031,
      league: 'UEFA Champions League — Semifinal',
      leagueId: 2,
      leagueLogo: 'https://media.api-sports.io/football/leagues/2.png',
      leagueCountry: 'World',
      home: {
        name: 'Bayern Munich',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/1/1b/FC_Bayern_M%C3%BCnchen_logo_%282017%29.svg',
        score: null,
      },
      away: {
        name: 'Inter Milan',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/0/05/FC_Internazionale_Milano_2021.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(29, 16, 0), // QUA 29/04 16:00 BRT
      venue: 'Allianz Arena, Munich',
      odds: { home: 2.00, draw: 3.40, away: 3.60 },
    },
    // ── Brasileirão Série A 2026 — Rodada 1 (12-13/04) ──────────────────────
    {
      id: 2003,
      league: 'Brasileirão Série A',
      leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Flamengo',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/9/96/Clube_de_Regatas_do_Flamengo_logo.svg',
        score: null,
      },
      away: {
        name: 'Palmeiras',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/1/10/Palmeiras_logo.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(12, 16, 0), // SAB 12/04 16:00 BRT
      venue: 'Estádio do Maracanã, Rio de Janeiro',
      odds: { home: 1.90, draw: 3.40, away: 4.00 },
    },
    {
      id: 2004,
      league: 'Brasileirão Série A',
      leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Corinthians',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/c/c8/SC_Corinthians_%28cropped%29.svg',
        score: null,
      },
      away: {
        name: 'São Paulo',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/6/6f/Brasao_do_Sao_Paulo_Futebol_Clube.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(12, 19, 0), // SAB 12/04 19:00 BRT
      venue: 'Neo Química Arena, São Paulo',
      odds: { home: 2.60, draw: 3.10, away: 2.80 },
    },
    {
      id: 2005,
      league: 'Brasileirão Série A',
      leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Botafogo',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/5/52/Botafogo_de_Futebol_e_Regatas_logo.svg',
        score: null,
      },
      away: {
        name: 'Fluminense',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/1/12/Fluminense_Football_Club.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(13, 16, 0), // DOM 13/04 16:00 BRT
      venue: 'Estádio Nilton Santos, Rio de Janeiro',
      odds: { home: 2.05, draw: 3.25, away: 3.55 },
    },
    {
      id: 2006,
      league: 'Brasileirão Série A',
      leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Atlético-MG',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/2/27/Clube_Atl%C3%A9tico_Mineiro_logo.svg',
        score: null,
      },
      away: {
        name: 'Internacional',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/f/f1/Escudo_do_Sport_Club_Internacional.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(13, 18, 30), // DOM 13/04 18:30 BRT
      venue: 'Arena MRV, Belo Horizonte',
      odds: { home: 1.80, draw: 3.50, away: 4.30 },
    },
    {
      id: 2007,
      league: 'Brasileirão Série A',
      leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Grêmio',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/0/08/Gremio_logo.svg',
        score: null,
      },
      away: {
        name: 'Cruzeiro',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/9/90/Cruzeiro_Esporte_Clube_%28logo%29.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(13, 20, 0), // DOM 13/04 20:00 BRT
      venue: 'Arena do Grêmio, Porto Alegre',
      odds: { home: 2.35, draw: 3.15, away: 3.00 },
    },
    // ── Copa do Brasil 2026 — 1ª Fase (16/04) ───────────────────────────────
    {
      id: 2008,
      league: 'Copa do Brasil',
      leagueId: 73,
      leagueLogo: 'https://media.api-sports.io/football/leagues/73.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Fortaleza',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/3/3d/Fortaleza_Esporte_Clube_logo.svg',
        score: null,
      },
      away: {
        name: 'Ceará',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/3/38/Cear%C3%A1_Sporting_Club_logo.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(16, 19, 30), // QUA 16/04 19:30 BRT
      venue: 'Arena Castelão, Fortaleza',
      odds: { home: 2.05, draw: 3.25, away: 3.60 },
    },
    {
      id: 2009,
      league: 'Copa do Brasil',
      leagueId: 73,
      leagueLogo: 'https://media.api-sports.io/football/leagues/73.png',
      leagueCountry: 'Brazil',
      home: {
        name: 'Santos',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/3/35/Santos_logo.svg',
        score: null,
      },
      away: {
        name: 'Mirassol',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/f/fd/Mirassol_Futebol_Clube_logo_%283_stars%29.png',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(16, 21, 0), // QUA 16/04 21:00 BRT
      venue: 'Vila Belmiro, Santos',
      odds: { home: 1.75, draw: 3.55, away: 4.80 },
    },
    // ── Copa Libertadores 2026 — Fase de Grupos (17/04) ─────────────────────
    {
      id: 2010,
      league: 'Copa Libertadores',
      leagueId: 13,
      leagueLogo: 'https://media.api-sports.io/football/leagues/13.png',
      leagueCountry: 'South America',
      home: {
        name: 'Flamengo',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/9/96/Clube_de_Regatas_do_Flamengo_logo.svg',
        score: null,
      },
      away: {
        name: 'River Plate',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/4/43/Club_Atl%C3%A9tico_River_Plate_logo.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(17, 19, 0), // QUI 17/04 19:00 BRT
      venue: 'Estádio do Maracanã, Rio de Janeiro',
      odds: { home: 1.90, draw: 3.50, away: 4.10 },
    },
    {
      id: 2011,
      league: 'Copa Libertadores',
      leagueId: 13,
      leagueLogo: 'https://media.api-sports.io/football/leagues/13.png',
      leagueCountry: 'South America',
      home: {
        name: 'Palmeiras',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/1/10/Palmeiras_logo.svg',
        score: null,
      },
      away: {
        name: 'Estudiantes',
        logo: 'https://upload.wikimedia.org/wikipedia/commons/b/b2/Estudiantes_de_la_Plata_crest_%282025%29.svg',
        score: null,
      },
      status: 'UPCOMING',
      minute: null,
      date: apr2026(17, 21, 0), // QUI 17/04 21:00 BRT
      venue: 'Allianz Parque, São Paulo',
      odds: { home: 1.75, draw: 3.55, away: 4.50 },
    },
  ],
};

// ---------------------------------------------------------------------------
// Standings fallback — Brasileirão 2026 (simulated)
// ---------------------------------------------------------------------------
export const FALLBACK_STANDINGS: StandingsCache = {
  source: 'fallback',
  updated_at: null,
  leagueId: 71,
  season: new Date().getFullYear(),
  standings: [
    { rank: 1,  team: 'Flamengo',     teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/9/91/CR_Flamengo.svg',     played: 20, won: 13, drawn: 4, lost: 3, goalsFor: 40, goalsAgainst: 20, goalDifference: 20, points: 43, form: 'WWDWW' },
    { rank: 2,  team: 'Palmeiras',    teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/1/10/Palmeiras_logo.svg',   played: 20, won: 12, drawn: 5, lost: 3, goalsFor: 38, goalsAgainst: 18, goalDifference: 20, points: 41, form: 'WWWDL' },
    { rank: 3,  team: 'Botafogo',     teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/5/52/Botafogo_de_Futebol_e_Regatas_logo.svg', played: 20, won: 12, drawn: 3, lost: 5, goalsFor: 36, goalsAgainst: 22, goalDifference: 14, points: 39, form: 'WDWWL' },
    { rank: 4,  team: 'Atletico-MG',  teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/2/27/Clube_Atl%C3%A9tico_Mineiro_logo.svg', played: 20, won: 11, drawn: 4, lost: 5, goalsFor: 34, goalsAgainst: 24, goalDifference: 10, points: 37, form: 'WDLWW' },
    { rank: 5,  team: 'Fluminense',   teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/1/12/Fluminense_Football_Club.svg', played: 20, won: 10, drawn: 5, lost: 5, goalsFor: 30, goalsAgainst: 22, goalDifference: 8,  points: 35, form: 'WWLDD' },
    { rank: 6,  team: 'Sao Paulo',    teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/6/6f/Brasao_do_Sao_Paulo_Futebol_Clube.svg', played: 20, won: 10, drawn: 4, lost: 6, goalsFor: 28, goalsAgainst: 23, goalDifference: 5,  points: 34, form: 'LWWDW' },
    { rank: 7,  team: 'Corinthians',  teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/e/e7/SC_Corinthians.svg',   played: 20, won: 9,  drawn: 6, lost: 5, goalsFor: 27, goalsAgainst: 22, goalDifference: 5,  points: 33, form: 'DWWDL' },
    { rank: 8,  team: 'Gremio',       teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/0/08/Gremio_logo.svg',       played: 20, won: 9,  drawn: 4, lost: 7, goalsFor: 29, goalsAgainst: 26, goalDifference: 3,  points: 31, form: 'LWDWW' },
    { rank: 9,  team: 'Internacional',teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/c/c5/Sport_Club_Internacional_logo.svg', played: 20, won: 8, drawn: 5, lost: 7, goalsFor: 25, goalsAgainst: 24, goalDifference: 1,  points: 29, form: 'DLWDW' },
    { rank: 10, team: 'Bragantino',   teamLogo: 'https://upload.wikimedia.org/wikipedia/fr/b/b3/Red_Bull_Bragantino_%28logo%29.svg', played: 20, won: 7, drawn: 6, lost: 7, goalsFor: 24, goalsAgainst: 25, goalDifference: -1, points: 27, form: 'WDLWD' },
  ],
};

// ---------------------------------------------------------------------------
// Top scorers fallback
// ---------------------------------------------------------------------------
export const FALLBACK_SCORERS: ScorersCache = {
  source: 'fallback',
  updated_at: null,
  leagueId: 71,
  season: new Date().getFullYear(),
  scorers: [
    { rank: 1,  player: 'Pedro',           team: 'Flamengo',   teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/9/91/CR_Flamengo.svg',     goals: 14, assists: 5,  photo: '' },
    { rank: 2,  player: 'Flaco Lopez',     team: 'Palmeiras',  teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/1/10/Palmeiras_logo.svg',   goals: 12, assists: 3,  photo: '' },
    { rank: 3,  player: 'Calleri',         team: 'Sao Paulo',  teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/6/6f/Brasao_do_Sao_Paulo_Futebol_Clube.svg', goals: 11, assists: 4, photo: '' },
    { rank: 4,  player: 'Tiquinho Soares', team: 'Botafogo',   teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/5/52/Botafogo_de_Futebol_e_Regatas_logo.svg', goals: 10, assists: 2, photo: '' },
    { rank: 5,  player: 'Cano',            team: 'Fluminense', teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/1/12/Fluminense_Football_Club.svg', goals: 9,  assists: 6, photo: '' },
    { rank: 6,  player: 'Hulk',            team: 'Atletico-MG',teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/2/27/Clube_Atl%C3%A9tico_Mineiro_logo.svg', goals: 9, assists: 3, photo: '' },
    { rank: 7,  player: 'Yuri Alberto',    team: 'Corinthians',teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/e/e7/SC_Corinthians.svg',   goals: 8,  assists: 2,  photo: '' },
    { rank: 8,  player: 'Estevao',         team: 'Palmeiras',  teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/1/10/Palmeiras_logo.svg',   goals: 7,  assists: 8,  photo: '' },
    { rank: 9,  player: 'Cristaldo',       team: 'Gremio',     teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/0/08/Gremio_logo.svg',       goals: 7,  assists: 4,  photo: '' },
    { rank: 10, player: 'Gabriel',         team: 'Flamengo',   teamLogo: 'https://upload.wikimedia.org/wikipedia/commons/9/91/CR_Flamengo.svg',       goals: 7,  assists: 7,  photo: '' },
  ],
};
