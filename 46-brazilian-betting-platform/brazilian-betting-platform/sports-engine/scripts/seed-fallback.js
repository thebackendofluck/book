#!/usr/bin/env node
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
// Seed fallback data into KV via wrangler CLI.
// Run after: wrangler kv:namespace create SPORTS_DATA
// Usage: node scripts/seed-fallback.js --namespace-id=<id>
// ---------------------------------------------------------------------------

'use strict';

const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const args = process.argv.slice(2);
const nsArg = args.find(a => a.startsWith('--namespace-id='));

if (!nsArg) {
  console.error('Usage: node scripts/seed-fallback.js --namespace-id=<KV_NAMESPACE_ID>');
  console.error('');
  console.error('Get your namespace ID by running: wrangler kv:namespace list');
  process.exit(1);
}

const namespaceId = nsArg.split('=')[1];

// Validate namespace-id is alphanumeric/hex only (Cloudflare KV IDs are 32-char hex)
if (!/^[a-f0-9]{32}$/i.test(namespaceId)) {
  console.error('Invalid namespace-id format. Expected a 32-character hex string.');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Fallback data mirrors src/fallback.ts — kept in sync manually.
// This is a plain-JS copy so the seed script runs without a build step.
// ---------------------------------------------------------------------------

const now = new Date().toISOString();

const FALLBACK_LIVE = {
  source: 'fallback',
  updated_at: null,
  fixtures: [
    {
      id: 1001,
      league: 'Brasileirao Serie A', leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: { name: 'Flamengo',  logo: 'https://upload.wikimedia.org/wikipedia/commons/9/91/CR_Flamengo.svg',   score: 1 },
      away: { name: 'Palmeiras', logo: 'https://upload.wikimedia.org/wikipedia/commons/1/10/Palmeiras_logo.svg', score: 0 },
      status: 'LIVE', minute: 62, date: now,
      venue: 'Estadio do Maracana, Rio de Janeiro',
      odds: { home: 1.75, draw: 3.40, away: 4.20 },
      events: [
        { minute: 23, type: 'Goal', team: 'home', player: 'Gabriel', detail: 'Normal Goal' },
        { minute: 55, type: 'Card', team: 'away', player: 'Murillo', detail: 'Yellow Card' },
      ],
      stats: { possession: { home: 54, away: 46 }, shots: { home: 8, away: 5 }, shotsOnTarget: { home: 4, away: 2 }, corners: { home: 5, away: 3 }, fouls: { home: 7, away: 9 } },
    },
    {
      id: 1002,
      league: 'Brasileirao Serie A', leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: { name: 'Corinthians', logo: 'https://upload.wikimedia.org/wikipedia/commons/e/e7/SC_Corinthians.svg', score: 0 },
      away: { name: 'Sao Paulo',   logo: 'https://upload.wikimedia.org/wikipedia/commons/6/6f/Brasao_do_Sao_Paulo_Futebol_Clube.svg', score: 0 },
      status: 'LIVE', minute: 34, date: now,
      venue: 'Neo Quimica Arena, Sao Paulo',
      odds: { home: 2.60, draw: 3.10, away: 2.80 },
    },
    {
      id: 1003,
      league: 'Brasileirao Serie A', leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png',
      leagueCountry: 'Brazil',
      home: { name: 'Botafogo',   logo: 'https://upload.wikimedia.org/wikipedia/commons/5/52/Botafogo_de_Futebol_e_Regatas_logo.svg', score: 2 },
      away: { name: 'Fluminense', logo: 'https://upload.wikimedia.org/wikipedia/commons/1/12/Fluminense_Football_Club.svg', score: 1 },
      status: 'LIVE', minute: 78, date: now,
      venue: 'Estadio Nilton Santos, Rio de Janeiro',
      odds: { home: 1.50, draw: 4.00, away: 6.50 },
    },
    {
      id: 1004,
      league: 'Copa Libertadores', leagueId: 13,
      leagueLogo: 'https://media.api-sports.io/football/leagues/13.png',
      leagueCountry: 'South America',
      home: { name: 'Flamengo',     logo: 'https://upload.wikimedia.org/wikipedia/commons/9/91/CR_Flamengo.svg', score: 1 },
      away: { name: 'Boca Juniors', logo: 'https://upload.wikimedia.org/wikipedia/commons/8/83/Escudo_del_Club_Atl%C3%A9tico_Boca_Juniors.svg', score: 1 },
      status: 'HT', minute: 45, date: now,
      venue: 'Estadio do Maracana, Rio de Janeiro',
      odds: { home: 2.10, draw: 3.20, away: 3.50 },
    },
    {
      id: 1005,
      league: 'La Liga', leagueId: 140,
      leagueLogo: 'https://media.api-sports.io/football/leagues/140.png',
      leagueCountry: 'Spain',
      home: { name: 'Real Madrid', logo: 'https://upload.wikimedia.org/wikipedia/en/5/56/Real_Madrid_CF.svg', score: 2 },
      away: { name: 'Barcelona',   logo: 'https://upload.wikimedia.org/wikipedia/en/4/47/FC_Barcelona_%28crest%29.svg', score: 2 },
      status: 'LIVE', minute: 88, date: now,
      venue: 'Estadio Santiago Bernabeu, Madrid',
      odds: { home: 2.20, draw: 3.30, away: 3.10 },
    },
  ],
};

const FALLBACK_UPCOMING = {
  source: 'fallback',
  updated_at: null,
  fixtures: [
    {
      id: 2001, league: 'Brasileirao Serie A', leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png', leagueCountry: 'Brazil',
      home: { name: 'Santos', logo: 'https://upload.wikimedia.org/wikipedia/commons/3/35/Santos_logo.svg', score: null },
      away: { name: 'Vasco',  logo: 'https://upload.wikimedia.org/wikipedia/commons/d/d2/CR_Vasco_da_Gama.svg', score: null },
      status: 'UPCOMING', minute: null, date: new Date(Date.now() + 1200000).toISOString(),
      venue: 'Vila Belmiro, Santos', odds: { home: 2.30, draw: 3.20, away: 2.90 },
    },
    {
      id: 2002, league: 'Brasileirao Serie A', leagueId: 71,
      leagueLogo: 'https://media.api-sports.io/football/leagues/71.png', leagueCountry: 'Brazil',
      home: { name: 'Internacional', logo: 'https://upload.wikimedia.org/wikipedia/commons/c/c5/Sport_Club_Internacional_logo.svg', score: null },
      away: { name: 'Cruzeiro',      logo: 'https://upload.wikimedia.org/wikipedia/commons/9/90/Cruzeiro_Esporte_Clube_%28logo%29.svg', score: null },
      status: 'UPCOMING', minute: null, date: new Date(Date.now() + 2700000).toISOString(),
      venue: 'Beira-Rio, Porto Alegre', odds: { home: 1.85, draw: 3.50, away: 4.00 },
    },
  ],
};

// ---------------------------------------------------------------------------
// Write keys to KV using execFileSync (no shell injection risk)
// ---------------------------------------------------------------------------
function kvPut(key, value) {
  const jsonStr = JSON.stringify(value);

  // Write value to a temp file to avoid any shell quoting issues
  const tmpFile = path.join(os.tmpdir(), `betbr-kv-${Date.now()}.json`);
  fs.writeFileSync(tmpFile, jsonStr, 'utf8');

  console.log(`  Writing key: ${key} (${jsonStr.length} bytes)`);
  try {
    execFileSync(
      'wrangler',
      ['kv:key', 'put', '--namespace-id', namespaceId, key, '--path', tmpFile],
      { stdio: 'pipe', cwd: path.join(__dirname, '..') }
    );
    console.log(`  OK: ${key}`);
  } catch (err) {
    console.error(`  FAILED: ${key}`);
    const msg = (err.stderr || err.stdout || Buffer.from('')).toString().trim();
    console.error(msg || err.message);
  } finally {
    try { fs.unlinkSync(tmpFile); } catch (_) { /* ignore */ }
  }
}

console.log('\nSeeding KV namespace: ' + namespaceId + '\n');
kvPut('live_fixtures', FALLBACK_LIVE);
kvPut('upcoming_fixtures', FALLBACK_UPCOMING);
console.log('\nDone. Standings and scorers are populated on first cron run.\n');
console.log('Next steps:');
console.log('  wrangler deploy');
console.log('  wrangler secret put API_FOOTBALL_KEY   # optional, enables live data');
