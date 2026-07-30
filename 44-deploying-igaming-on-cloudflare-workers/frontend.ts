// Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * AcmeToCasino — Frontend HTML served from Cloudflare Edge
 * Multi-game lobby: 10 unique game engines, all using Web Crypto API for PRNG
 * innerHTML is used only with static emoji/HTML literals — never with user data
 */

import { Env } from './utils.js';

interface GameRow {
  game_id: string;
  name: string;
  category: string;
  provider: string;
  rtp: number;
}

/** Map a game row to its client-side engine name */
function resolveEngine(g: GameRow): string {
  const name = g.name.toLowerCase();
  const cat  = g.category.toLowerCase();
  if (name.includes('aviator') || name.includes('crash') || cat === 'crash') return 'aviator';
  if (name.includes('mine') || name.includes('gem'))                          return 'mines';
  if (name.includes('plinko'))                                                 return 'plinko';
  if (name.includes('blackjack') || name.includes('pontoon') || name.includes('21')) return 'blackjack';
  if (name.includes('roulette') || name.includes('lightning') || name.includes('crazy time')) return 'roulette';
  if (name.includes('hi-lo') || name.includes('hilo') || name.includes('higher') || name.includes('lower')) return 'hilo';
  if (name.includes('tower') || name.includes('climb'))                        return 'tower';
  if (name.includes('baccarat') || name.includes('texas') || name.includes('poker') || cat === 'table') return 'blackjack';
  if (name.includes('double') || name.includes('coin') || name.includes('flip'))  return 'coinflip';
  if (cat === 'slots' || name.includes('tiger') || name.includes('bonanza') || name.includes('starburst') ||
      name.includes('wolf') || name.includes('book') || name.includes('moolah') || name.includes('slot')) return 'slots';
  if (cat === 'live') return 'roulette';
  return 'dice';
}

export async function serveFrontend(env: Env): Promise<Response> {
  let gameRows: GameRow[] = [];
  try {
    const res = await env.DB.prepare(
      'SELECT game_id, name, category, provider, rtp FROM games WHERE is_active = 1 ORDER BY name'
    ).all();
    gameRows = (res.results ?? []) as GameRow[];
  } catch (_e) { /* DB may not be seeded yet */ }

  const gameCards = gameRows.map((g) => {
    const colors: Record<string, string> = {
      slots: '#f2ca50', table: '#c1c1ff', live: '#00e676',
      crash: '#eabfff', instant: '#18ffff',
    };
    const engineIcons: Record<string, string> = {
      aviator: 'rocket_launch', mines: 'diamond', plinko: 'sports_baseball',
      blackjack: 'style', roulette: 'radio_button_unchecked', hilo: 'swap_vert',
      tower: 'stacked_line_chart', slots: 'casino', dice: 'casino', coinflip: 'paid',
    };
    const color  = colors[g.category] || '#f2ca50';
    const engine = resolveEngine(g);
    const icon   = engineIcons[engine] || 'casino';
    // All values come from our own D1 — safe to interpolate via encodeForHTML
    return [
      '<div class="gc" onclick="playGame(\'', encodeForHTML(g.game_id), '\',\'', encodeForHTML(engine), '\')">',
      '<div class="gc-top" style="background:linear-gradient(135deg,', color, '22,#111124)">',
      '<span class="mi" style="font-size:2rem;color:', color, '">', icon, '</span></div>',
      '<div class="gc-info"><div class="gc-name">', encodeForHTML(g.name), '</div>',
      '<div class="gc-meta">', encodeForHTML(g.provider), ' \xb7 RTP ', String(g.rtp), '%</div></div></div>',
    ].join('');
  }).join('');

  return new Response(buildHTML(gameRows.length, gameCards), {
    status: 200,
    headers: { 'Content-Type': 'text/html;charset=UTF-8' },
  });
}

function encodeForHTML(str: string): string {
  return str
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function buildHTML(gameCount: number, gameCards: string): string {
/* ─────────────────────────────────────────────────────────────────────────
   NOTE: All innerHTML assignments in the inline <script> below operate
   exclusively on static emoji/HTML literals — never on user-supplied data.
   User-facing strings (game names, provider, etc.) flow through
   encodeForHTML() above and are baked into the server-rendered HTML.
   ───────────────────────────────────────────────────────────────────────── */
return `<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AcmeToCasino \u2014 Cloudflare Edge Platform</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0c0c1e;--card:#1e1e31;--gold:#f2ca50;--text:#e2e0fa;--dim:#99907c;--border:#4d463522;--green:#00e676;--red:#ff5252;--purple:#c1c1ff}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.mi{font-family:'Material Symbols Outlined';font-variation-settings:'FILL' 0,'wght' 400}
nav{background:#0c0c1ecc;backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:50}
.logo{font-family:'Space Grotesk';font-size:1.4rem;font-weight:700;color:var(--gold);letter-spacing:1px}
.badge{background:#f2ca5020;color:var(--gold);padding:4px 12px;border-radius:20px;font-size:0.7rem;font-weight:600}
.hero{text-align:center;padding:60px 24px 40px}
.hero h1{font-family:'Space Grotesk';font-size:2.5rem;font-weight:700;background:linear-gradient(135deg,#f2ca50,#e2e0fa,#c1c1ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}
.hero p{color:var(--dim);max-width:600px;margin:0 auto 24px;font-size:0.95rem}
.stats{display:flex;gap:24px;justify-content:center;flex-wrap:wrap;margin-bottom:40px}
.stat{text-align:center}.stat-val{font-family:'Space Grotesk';font-size:1.8rem;font-weight:700;color:var(--gold)}.stat-label{font-size:0.7rem;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
.section-title{font-family:'Space Grotesk';font-size:1.2rem;color:var(--text);padding:0 24px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.section-title .mi{color:var(--gold)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;padding:0 24px 40px}
.gc{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;cursor:pointer;transition:all .3s}
.gc:hover{transform:translateY(-4px);border-color:var(--gold);box-shadow:0 8px 30px #f2ca5015}
.gc-top{height:100px;display:flex;align-items:center;justify-content:center}
.gc-info{padding:10px 12px}
.gc-name{font-weight:600;font-size:0.85rem;margin-bottom:2px}
.gc-meta{font-size:0.65rem;color:var(--dim)}
.game-panel{display:none;padding:24px;max-width:640px;margin:0 auto}
.game-panel.active{display:block}
.game-box{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;text-align:center}
.game-box h2{font-family:'Space Grotesk';font-size:1.4rem;color:var(--gold);margin-bottom:6px}
.game-subtitle{color:var(--dim);font-size:0.75rem;margin-bottom:20px}
.bet-row{display:flex;gap:8px;justify-content:center;align-items:center;margin:14px 0;flex-wrap:wrap}
.bet-input{background:#111124;border:1px solid var(--border);color:var(--text);padding:10px 16px;border-radius:8px;font-size:1rem;width:110px;text-align:center;outline:none}
.bet-input:focus{border-color:var(--gold)}
.btn{font-family:'Space Grotesk';font-weight:700;padding:11px 28px;border:none;border-radius:10px;cursor:pointer;font-size:0.88rem;transition:all .2s}
.btn-gold{background:linear-gradient(135deg,#f2ca50,#d4af37);color:#0c0c1e}
.btn-gold:hover:not(:disabled){box-shadow:0 0 20px #f2ca5044;transform:scale(1.02)}
.btn-gold:disabled,.btn-green:disabled,.btn-red:disabled{opacity:.5;cursor:not-allowed;transform:none!important;box-shadow:none!important}
.btn-green{background:linear-gradient(135deg,#00e676,#00b248);color:#0c0c1e}
.btn-green:hover:not(:disabled){box-shadow:0 0 20px #00e67644;transform:scale(1.02)}
.btn-red{background:linear-gradient(135deg,#ff5252,#c62828);color:#fff}
.btn-red:hover:not(:disabled){box-shadow:0 0 20px #ff525244;transform:scale(1.02)}
.btn-outline{background:transparent;border:2px solid var(--border);color:var(--text)}
.btn-outline:hover:not(:disabled){border-color:var(--gold);color:var(--gold)}
.choice-row{display:flex;gap:10px;justify-content:center;margin:12px 0;flex-wrap:wrap}
.choice{padding:9px 20px;border:2px solid var(--border);border-radius:10px;cursor:pointer;font-weight:600;font-size:0.85rem;transition:all .2s}
.choice:hover,.choice.active{border-color:var(--gold);color:var(--gold);background:#f2ca5010}
.result-msg{margin-top:14px;font-size:1.05rem;font-weight:600;min-height:24px}
.result-msg.win{color:var(--green)}.result-msg.lose{color:var(--red)}
.balance-bar{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 20px;margin:0 24px 20px;display:flex;justify-content:space-between;align-items:center}
.bal-label{font-size:0.75rem;color:var(--dim)}.bal-val{font-family:'Space Grotesk';font-size:1.2rem;font-weight:700;color:var(--green)}
.history{margin-top:14px;max-height:130px;overflow-y:auto;font-size:0.72rem;color:var(--dim);text-align:left}
.history div{padding:3px 0;border-bottom:1px solid #ffffff06}
.back-btn{display:inline-flex;align-items:center;gap:4px;color:var(--dim);font-size:0.85rem;cursor:pointer;margin-bottom:16px;transition:color .2s}
.back-btn:hover{color:var(--gold)}
footer{text-align:center;padding:40px 24px;color:var(--dim);font-size:0.7rem;border-top:1px solid var(--border)}
footer a{color:var(--gold);text-decoration:none}
.edge-badge{display:inline-flex;align-items:center;gap:6px;background:#00e67615;color:#00e676;padding:6px 16px;border-radius:20px;font-size:0.75rem;font-weight:600;margin-bottom:20px}
.edge-badge .mi{font-size:14px}
/* DICE */
.dice-display{font-size:4.5rem;margin:16px 0;transition:all .3s;user-select:none}
@keyframes spin-dice{0%{transform:rotate(0) scale(1)}50%{transform:rotate(180deg) scale(1.2)}100%{transform:rotate(360deg) scale(1)}}
.dice-display.rolling{animation:spin-dice .25s linear infinite}
/* AVIATOR */
.av-screen{background:#111124;border-radius:12px;padding:24px;margin:16px 0;position:relative;min-height:180px;display:flex;flex-direction:column;align-items:center;justify-content:center}
.av-mult{font-family:'Space Grotesk';font-size:3.5rem;font-weight:700;color:var(--gold);text-shadow:0 0 30px #f2ca5066;transition:color .2s}
.av-mult.crashed{color:var(--red);text-shadow:0 0 30px #ff525266}
.av-mult.cashed{color:var(--green);text-shadow:0 0 30px #00e67666}
.plane{font-size:2rem;position:absolute;top:16px;right:24px}
@keyframes fly-up{0%{transform:translateY(0) rotate(-20deg)}100%{transform:translateY(-20px) rotate(-35deg)}}
.plane.flying{animation:fly-up 1s ease-in-out infinite alternate}
@keyframes crash-anim{0%{transform:rotate(0)}100%{transform:rotate(90deg) scale(0.5)}}
.plane.crashed{animation:crash-anim .4s forwards}
/* MINES */
.mines-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin:12px auto;max-width:280px}
.mine-cell{background:#111124;border:2px solid var(--border);border-radius:8px;height:48px;display:flex;align-items:center;justify-content:center;font-size:1.4rem;cursor:pointer;transition:all .2s;user-select:none}
.mine-cell:hover:not(.rev){border-color:var(--gold);background:#f2ca5015;transform:scale(1.05)}
.mine-cell.rev.gem{border-color:var(--green);background:#00e67615;cursor:default}
.mine-cell.rev.mine{border-color:var(--red);background:#ff525215;cursor:default;animation:shake .3s}
.mine-cell.rev.safe{border-color:var(--dim);cursor:default;opacity:.5}
@keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-4px)}75%{transform:translateX(4px)}}
.mines-info{display:flex;gap:16px;justify-content:center;font-size:0.8rem;color:var(--dim);margin-bottom:8px}
.mines-info strong{color:var(--gold)}
/* PLINKO */
.plinko-screen{background:#111124;border-radius:12px;padding:16px;margin:14px 0}
.plinko-canvas{width:100%;height:260px;position:relative;overflow:hidden}
.pp{position:absolute;width:8px;height:8px;border-radius:50%;background:#4d4635aa}
.pb{position:absolute;width:14px;height:14px;border-radius:50%;background:var(--gold);box-shadow:0 0 10px #f2ca5099;z-index:10}
.plinko-slots{display:flex;gap:2px;margin-top:4px}
.ps{flex:1;padding:4px 2px;background:#111124;border:1px solid var(--border);border-radius:4px;text-align:center;font-size:0.6rem;font-weight:700;color:var(--dim)}
.ps.hit{border-color:var(--gold);color:var(--gold);background:#f2ca5015}
/* BLACKJACK */
.cards-area{display:flex;flex-direction:column;gap:12px;margin:14px 0}
.cards-row{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;align-items:center}
.card{width:52px;height:76px;background:#fff;border-radius:6px;display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:4px;font-family:'Space Grotesk';font-weight:700;font-size:1.1rem;box-shadow:0 2px 8px #00000044}
.card.red{color:#c62828}.card.black{color:#111}
.card.face-down{background:linear-gradient(135deg,#1e1e31,#0c0c1e);border:2px solid var(--border)}
.card-suit{font-size:0.75rem;align-self:flex-end}
.cards-label{font-size:0.7rem;color:var(--dim);text-align:left;margin-left:4px;margin-bottom:2px}
.bj-score{font-family:'Space Grotesk';font-weight:700;font-size:1rem;color:var(--purple);margin-left:8px}
.bj-buttons{display:flex;gap:8px;justify-content:center;margin:12px 0;flex-wrap:wrap}
/* ROULETTE */
.rw{width:160px;height:160px;border-radius:50%;border:6px solid #d4af37;margin:10px auto;display:flex;align-items:center;justify-content:center;font-size:2.5rem;background:conic-gradient(#c62828 0 10deg,#111 10deg 20deg,#c62828 20deg 30deg,#111 30deg 40deg,#c62828 40deg 50deg,#111 50deg 60deg,#c62828 60deg 70deg,#111 70deg 80deg,#c62828 80deg 90deg,#111 90deg 100deg,#c62828 100deg 110deg,#111 110deg 120deg,#c62828 120deg 130deg,#111 130deg 140deg,#c62828 140deg 150deg,#111 150deg 160deg,#c62828 160deg 170deg,#111 170deg 180deg,#c62828 180deg 190deg,#111 190deg 200deg,#c62828 200deg 210deg,#111 210deg 220deg,#c62828 220deg 230deg,#111 230deg 240deg,#c62828 240deg 250deg,#111 250deg 260deg,#c62828 260deg 270deg,#111 270deg 280deg,#c62828 280deg 290deg,#111 290deg 300deg,#c62828 300deg 310deg,#111 310deg 320deg,#c62828 320deg 330deg,#111 330deg 340deg,#00b248 340deg 360deg);box-shadow:0 0 30px #f2ca5033;transition:transform 3s cubic-bezier(.17,.67,.12,1)}
.rn-grid{display:grid;grid-template-columns:repeat(9,1fr);gap:3px;margin:10px auto;max-width:360px}
.rn{padding:5px 2px;border-radius:4px;font-size:0.65rem;font-weight:700;cursor:pointer;transition:all .2s;border:2px solid transparent;text-align:center}
.rn.rr{background:#c6282820;color:#ff6b6b}.rn.rb{background:#33333340;color:var(--text)}.rn.rg{background:#00b24820;color:var(--green)}
.rn:hover,.rn.sel{border-color:var(--gold);transform:scale(1.1)}
.r-bets{display:flex;gap:8px;justify-content:center;margin:8px 0;flex-wrap:wrap}
/* HI-LO */
.hilo-card-area{display:flex;gap:20px;justify-content:center;align-items:center;margin:16px 0}
.big-card{width:90px;height:130px;background:#fff;border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:'Space Grotesk';font-weight:700;font-size:2rem;box-shadow:0 4px 16px #00000066;position:relative}
.big-card.red{color:#c62828}.big-card.black{color:#111}
.big-card .small-suit{position:absolute;top:6px;left:8px;font-size:0.8rem}
.big-card.fd{background:linear-gradient(135deg,#1e1e31,#0c0c1e);border:2px solid var(--border)}
.hilo-info{font-family:'Space Grotesk';font-size:0.95rem;color:var(--purple);margin:6px 0}
.hilo-mult{color:var(--gold);font-size:1.3rem;font-weight:700}
/* TOWER */
.tower-wrap{display:flex;gap:8px;justify-content:center;margin:14px 0}
.tower-mult-col{display:flex;flex-direction:column;gap:4px;width:44px}
.tml{height:38px;display:flex;align-items:center;font-size:0.7rem;color:var(--gold);font-weight:700}
.tower-col{display:flex;flex-direction:column;gap:4px;width:72px}
.tc{height:38px;border:2px solid var(--border);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1rem;background:#111124;transition:all .2s}
.tc.ar{border-color:var(--gold);background:#f2ca5010;cursor:pointer}
.tc.ar:hover{background:#f2ca5025;transform:scale(1.05);cursor:pointer}
.tc.safe{border-color:var(--green);background:#00e67615;cursor:default}
.tc.dead{border-color:var(--red);background:#ff525215;cursor:default;animation:shake .3s}
.tc.passed{border-color:var(--dim);background:#33333320;opacity:.6;cursor:default}
/* SLOTS */
.slots-display{display:flex;gap:8px;justify-content:center;margin:16px 0}
.reel{width:72px;height:86px;background:#111124;border:2px solid var(--border);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:2.4rem;overflow:hidden}
@keyframes reel-spin{0%{transform:translateY(-200%)}100%{transform:translateY(200%)}}
.reel.spinning span{display:inline-block;animation:reel-spin .15s linear infinite}
/* COIN FLIP */
.coin{width:100px;height:100px;border-radius:50%;background:linear-gradient(135deg,#f2ca50,#d4af37);display:flex;align-items:center;justify-content:center;font-size:2.4rem;margin:16px auto;box-shadow:0 0 20px #f2ca5044}
@keyframes coin-flip{0%{transform:rotateY(0) scale(1)}50%{transform:rotateY(900deg) scale(0.8)}100%{transform:rotateY(1800deg) scale(1)}}
.coin.flipping{animation:coin-flip .8s ease-in-out forwards}
</style></head><body>
<nav>
  <span class="logo">ACMETOCASINO</span>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="badge">Cloudflare Edge</span>
    <span class="badge" style="background:#00e67615;color:#00e676"><span class="mi" style="font-size:12px;vertical-align:middle">cloud_done</span> Live</span>
  </div>
</nav>
<div id="lobby">
  <div class="hero">
    <div class="edge-badge"><span class="mi">speed</span> Served from Cloudflare Edge &middot; &lt;20ms global latency</div>
    <h1>AcmeToCasino Platform</h1>
    <p>Full-stack iGaming platform on Cloudflare Workers, D1, and KV. Zero servers. Global edge. Production-grade.</p>
    <div class="stats">
      <div class="stat"><div class="stat-val">${gameCount}</div><div class="stat-label">Games</div></div>
      <div class="stat"><div class="stat-val">9</div><div class="stat-label">DB Tables</div></div>
      <div class="stat"><div class="stat-val">&lt;20ms</div><div class="stat-label">Latency</div></div>
      <div class="stat"><div class="stat-val">300+</div><div class="stat-label">Edge Locations</div></div>
    </div>
  </div>
  <div class="balance-bar">
    <span class="bal-label">Demo Balance</span>
    <span class="bal-val" id="balDisplay">$1,000.00</span>
  </div>
  <div class="section-title"><span class="mi">casino</span> Game Lobby</div>
  <div class="grid">${gameCards}</div>
</div>

<div class="game-panel" id="gamePanel">
  <div class="back-btn" onclick="backToLobby()"><span class="mi" style="font-size:18px">arrow_back</span> Back to Lobby</div>
  <div id="gameContainer"></div>
</div>

<footer>
  <p>AcmeToCasino Platform &middot; Cloudflare Workers + D1 + KV &middot; <a href="/health">API Health</a> &middot; <a href="/api/games">Games API</a></p>
  <p style="margin-top:8px">Part of <a href="https://thebackendofluck.com" target="_blank">The Backend of Luck</a> &mdash; The definitive guide to iGaming infrastructure</p>
</footer>

<script>
// ─── Global state ────────────────────────────────────────────────────────────
var balance = 1000;
function fmt(n) { return n.toLocaleString('en-US', {style:'currency', currency:'USD'}); }
function fmtX(m) { return m.toFixed(2) + 'x'; }
function updateBal() { document.getElementById('balDisplay').textContent = fmt(balance); }
function delay(ms) { return new Promise(function(r){ setTimeout(r, ms); }); }

// All randomness via Web Crypto API
function rnd() { var a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] / (0xFFFFFFFF + 1); }
function rndInt(lo, hi) { return Math.floor(rnd() * (hi - lo + 1)) + lo; }

function addHist(el, txt) {
  var d = document.createElement('div');
  d.textContent = new Date().toLocaleTimeString() + ' | ' + txt;
  el.insertBefore(d, el.firstChild);
}
function getBet(id) { return Math.max(1, parseFloat(document.getElementById(id).value) || 10); }
function checkBal(bet, msgId) {
  if (bet > balance) {
    var m = document.getElementById(msgId);
    m.textContent = 'Insufficient balance!'; m.className = 'result-msg lose'; return false;
  }
  return true;
}

// ─── Navigation ──────────────────────────────────────────────────────────────
function playGame(id, engine) {
  document.getElementById('lobby').style.display = 'none';
  document.getElementById('gamePanel').classList.add('active');
  if (window._avIv) { clearInterval(window._avIv); window._avIv = null; }
  var container = document.getElementById('gameContainer');
  // Clear previous content safely using DOM
  while (container.firstChild) container.removeChild(container.firstChild);
  var title = id.replace(/-/g,' ').replace(/\b\w/g, function(c){ return c.toUpperCase(); });
  var fns = {
    dice: renderDice, aviator: renderAviator, mines: renderMines,
    plinko: renderPlinko, blackjack: renderBlackjack, roulette: renderRoulette,
    hilo: renderHiLo, tower: renderTower, slots: renderSlots, coinflip: renderCoinFlip
  };
  (fns[engine] || renderDice)(container, title);
}

function backToLobby() {
  if (window._avIv) { clearInterval(window._avIv); window._avIv = null; }
  document.getElementById('lobby').style.display = 'block';
  document.getElementById('gamePanel').classList.remove('active');
  var container = document.getElementById('gameContainer');
  while (container.firstChild) container.removeChild(container.firstChild);
}

// Helper: create element with optional classes and text
function el(tag, cls, txt) {
  var e = document.createElement(tag); if (cls) e.className = cls; if (txt) e.textContent = txt; return e;
}
// Helper: create button
function btn(cls, label, handler) {
  var b = document.createElement('button'); b.className = 'btn ' + cls; b.textContent = label;
  b.onclick = handler; return b;
}
// Helper: create number input
function numInput(id, val) {
  var i = document.createElement('input'); i.type = 'number'; i.className = 'bet-input';
  i.id = id; i.value = val; i.min = '1'; return i;
}

function gameBox(container, title, subtitle) {
  var box = el('div', 'game-box');
  var h2 = el('h2', null, title); box.appendChild(h2);
  var sub = el('p', 'game-subtitle', subtitle); box.appendChild(sub);
  container.appendChild(box); return box;
}

// ─── 1. DICE ─────────────────────────────────────────────────────────────────
function renderDice(container, title) {
  var box = gameBox(container, title, 'Provably fair \xb7 Pick Over or Under 50 \xb7 Web Crypto PRNG');
  var disp = el('div', 'dice-display', '\u{1F3B2}'); disp.id = 'diceDisp'; box.appendChild(disp);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('diceBet', '10'));
  box.appendChild(betRow);

  var choiceRow = el('div', 'choice-row');
  var cOver = el('div', 'choice active', '\u2B06 Over 50'); cOver.id = 'dOver';
  cOver.onclick = function(){ _dc='over'; cOver.classList.add('active'); cUnder.classList.remove('active'); };
  var cUnder = el('div', 'choice', '\u2B07 Under 50'); cUnder.id = 'dUnder';
  cUnder.onclick = function(){ _dc='under'; cUnder.classList.add('active'); cOver.classList.remove('active'); };
  choiceRow.appendChild(cOver); choiceRow.appendChild(cUnder); box.appendChild(choiceRow);

  var rollBtn = btn('btn-gold', 'Roll Dice', function(){ diceRoll(rollBtn); });
  box.appendChild(rollBtn);
  var msg = el('div', 'result-msg'); msg.id = 'diceMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'diceHist'; box.appendChild(hist);
  window._dc = 'over';
}

async function diceRoll(rollBtn) {
  var bet = getBet('diceBet');
  if (!checkBal(bet, 'diceMsg')) return;
  rollBtn.disabled = true; rollBtn.textContent = 'Rolling...';
  var disp = document.getElementById('diceDisp'); disp.classList.add('rolling');
  await delay(600);
  var roll = rndInt(1, 100); disp.classList.remove('rolling');
  var emojis = ['\u2680','\u2681','\u2682','\u2683','\u2684','\u2685'];
  disp.textContent = emojis[roll % 6] + ' ' + roll;
  var won = (window._dc === 'over' && roll > 50) || (window._dc === 'under' && roll < 50);
  var payout = won ? bet * 1.98 : 0;
  balance += won ? payout - bet : -bet; updateBal();
  var msg = document.getElementById('diceMsg');
  msg.textContent = won ? 'WIN! Roll: ' + roll + '  +' + fmt(payout) : 'LOSE. Roll: ' + roll + '  -' + fmt(bet);
  msg.className = 'result-msg ' + (won ? 'win' : 'lose');
  addHist(document.getElementById('diceHist'), 'Roll:' + roll + ' | ' + window._dc + ' | ' + (won ? '+'+fmt(payout) : '-'+fmt(bet)));
  rollBtn.disabled = false; rollBtn.textContent = 'Roll Dice';
}

// ─── 2. AVIATOR (Crash) ──────────────────────────────────────────────────────
function renderAviator(container, title) {
  var box = gameBox(container, title, 'Multiplier climbs until crash \xb7 Cash out before it falls!');
  var screen = el('div', 'av-screen');
  var plane = el('div', 'plane', '\u2708\uFE0F'); plane.id = 'avPlane'; screen.appendChild(plane);
  var mult = el('div', 'av-mult', '1.00x'); mult.id = 'avMult'; screen.appendChild(mult);
  var status = el('div', null, 'Place your bet and fly'); status.id = 'avStatus'; status.style.cssText = 'font-size:0.8rem;color:var(--dim);margin-top:6px'; screen.appendChild(status);
  box.appendChild(screen);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('avBet', '10'));
  box.appendChild(betRow);

  var btnRow = el('div', 'bet-row');
  var flyBtn = btn('btn-gold', '\u2708\uFE0F Fly!', null);
  var cashBtn = btn('btn-green', 'Cash Out', null); cashBtn.disabled = true;
  flyBtn.onclick = function(){ avStart(flyBtn, cashBtn); };
  cashBtn.onclick = function(){ avCashOut(flyBtn, cashBtn); };
  btnRow.appendChild(flyBtn); btnRow.appendChild(cashBtn); box.appendChild(btnRow);

  var msg = el('div', 'result-msg'); msg.id = 'avMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'avHist'; box.appendChild(hist);
  window._avState = 'idle';
}

function avStart(flyBtn, cashBtn) {
  var bet = getBet('avBet');
  if (!checkBal(bet, 'avMsg')) return;
  balance -= bet; updateBal();
  window._avBet = bet;
  var u = rnd(); window._avCrash = u < 0.01 ? 1.0 : Math.max(1.0, 0.99 / u);
  window._avMult = 1.0; window._avState = 'flying';
  flyBtn.disabled = true; cashBtn.disabled = false;
  document.getElementById('avPlane').className = 'plane flying';
  document.getElementById('avMult').className = 'av-mult';
  document.getElementById('avMult').textContent = '1.00x';
  document.getElementById('avMsg').textContent = '';
  document.getElementById('avStatus').textContent = 'Flying\u2026 cash out any time!';
  if (window._avIv) clearInterval(window._avIv);
  window._avIv = setInterval(function(){
    if (window._avState !== 'flying') return;
    window._avMult = parseFloat((window._avMult + window._avMult * 0.02).toFixed(2));
    document.getElementById('avMult').textContent = fmtX(window._avMult);
    if (window._avMult >= window._avCrash) avCrash(flyBtn, cashBtn);
  }, 80);
}

function avCashOut(flyBtn, cashBtn) {
  if (window._avState !== 'flying') return;
  window._avState = 'cashed'; clearInterval(window._avIv);
  var payout = window._avBet * window._avMult; balance += payout; updateBal();
  document.getElementById('avMult').className = 'av-mult cashed';
  document.getElementById('avPlane').className = 'plane';
  document.getElementById('avStatus').textContent = 'Cashed out!';
  cashBtn.disabled = true; flyBtn.disabled = false;
  var msg = document.getElementById('avMsg');
  msg.textContent = 'WIN! Cashed at ' + fmtX(window._avMult) + '  +' + fmt(payout); msg.className = 'result-msg win';
  addHist(document.getElementById('avHist'), 'Crash:' + fmtX(window._avCrash) + ' | Out:' + fmtX(window._avMult) + ' | +' + fmt(payout));
}

function avCrash(flyBtn, cashBtn) {
  window._avState = 'crashed'; clearInterval(window._avIv);
  document.getElementById('avMult').className = 'av-mult crashed';
  document.getElementById('avMult').textContent = 'CRASH! ' + fmtX(window._avCrash);
  document.getElementById('avPlane').className = 'plane crashed';
  document.getElementById('avStatus').textContent = 'Crashed!';
  cashBtn.disabled = true; flyBtn.disabled = false;
  var msg = document.getElementById('avMsg');
  msg.textContent = 'CRASHED at ' + fmtX(window._avCrash) + '  -' + fmt(window._avBet); msg.className = 'result-msg lose';
  addHist(document.getElementById('avHist'), 'Crashed:' + fmtX(window._avCrash) + ' | -' + fmt(window._avBet));
}

// ─── 3. MINES ────────────────────────────────────────────────────────────────
function renderMines(container, title) {
  var box = gameBox(container, title, 'Reveal gems to grow multiplier \xb7 Avoid the mines!');

  var info = el('div', 'mines-info');
  var mineCountSpan = el('strong', null, '5'); mineCountSpan.id = 'mCnt';
  var revSpan = el('strong', null, '0'); revSpan.id = 'mRev';
  var multSpan = el('strong', null, '1.00x'); multSpan.id = 'mMult';
  var i1 = el('span'); i1.appendChild(document.createTextNode('Mines: ')); i1.appendChild(mineCountSpan);
  var i2 = el('span'); i2.appendChild(document.createTextNode('Gems: ')); i2.appendChild(revSpan);
  var i3 = el('span'); i3.appendChild(document.createTextNode('Mult: ')); i3.appendChild(multSpan);
  info.appendChild(i1); info.appendChild(i2); info.appendChild(i3); box.appendChild(info);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Mines:'));
  var sel = document.createElement('select'); sel.className = 'bet-input'; sel.id = 'mSel'; sel.style.width = '70px';
  ['3','5','7','10','15'].forEach(function(v){ var o = document.createElement('option'); o.value = v; o.textContent = v; if (v==='5') o.selected = true; sel.appendChild(o); });
  betRow.appendChild(sel);
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('mBet', '10'));
  box.appendChild(betRow);

  var grid = el('div', 'mines-grid'); grid.id = 'mGrid'; box.appendChild(grid);

  var btnRow = el('div', 'bet-row');
  var cashBtn = btn('btn-green', 'Cash Out', null); cashBtn.disabled = true; cashBtn.id = 'mCash';
  var startBtn = btn('btn-gold', 'Start Game', null);
  startBtn.onclick = function(){ mineStart(startBtn, cashBtn); };
  cashBtn.onclick = function(){ mineCash(startBtn, cashBtn); };
  btnRow.appendChild(startBtn); btnRow.appendChild(cashBtn); box.appendChild(btnRow);

  var msg = el('div', 'result-msg'); msg.id = 'mMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'mHist'; box.appendChild(hist);
  mineInitGrid(grid);
  window._mState = 'idle';
}

function mineInitGrid(grid) {
  if (!grid) grid = document.getElementById('mGrid');
  while (grid.firstChild) grid.removeChild(grid.firstChild);
  for (var i = 0; i < 25; i++) {
    var cell = el('div', 'mine-cell', '?');
    cell.dataset.idx = String(i);
    cell.onclick = (function(idx){ return function(){ mineReveal(idx); }; })(i);
    grid.appendChild(cell);
  }
}

function mineStart(startBtn, cashBtn) {
  var bet = getBet('mBet');
  if (!checkBal(bet, 'mMsg')) return;
  balance -= bet; updateBal();
  var numM = parseInt(document.getElementById('mSel').value);
  var positions = [];
  while (positions.length < numM) { var p = rndInt(0, 24); if (positions.indexOf(p) === -1) positions.push(p); }
  window._mBoard = [];
  for (var i = 0; i < 25; i++) window._mBoard.push(positions.indexOf(i) !== -1 ? 'mine' : 'gem');
  window._mRev = 0; window._mBet = bet; window._mNumM = numM; window._mState = 'playing';
  startBtn.disabled = true; cashBtn.disabled = true;
  document.getElementById('mMsg').textContent = '';
  document.getElementById('mSel').disabled = true;
  document.getElementById('mRev').textContent = '0';
  document.getElementById('mMult').textContent = '1.00x';
  document.getElementById('mCnt').textContent = String(numM);
  mineInitGrid(document.getElementById('mGrid'));
}

function mineMultCalc(rev, numM) {
  var p = 1;
  for (var i = 0; i < rev; i++) p *= (25 - numM - i) / (25 - i);
  return Math.max(1.0, parseFloat((0.97 / p).toFixed(2)));
}

function mineReveal(idx) {
  if (window._mState !== 'playing') return;
  var cells = document.querySelectorAll('.mine-cell');
  var cell = cells[idx];
  if (cell.classList.contains('rev')) return;
  cell.classList.add('rev');
  if (window._mBoard[idx] === 'mine') {
    cell.textContent = '\uD83D\uDCA5'; cell.classList.add('mine');
    // Reveal all: mines as skull, safe as gem (no user data)
    for (var i = 0; i < 25; i++) {
      if (i === idx) continue;
      cells[i].classList.add('rev');
      cells[i].textContent = window._mBoard[i] === 'mine' ? '\uD83D\uDCA3' : '\uD83D\uDC8E';
      cells[i].classList.add(window._mBoard[i] === 'mine' ? 'mine' : 'safe');
    }
    window._mState = 'over';
    document.getElementById('mCash').disabled = true;
    document.getElementById('mSel').disabled = false;
    var startBtn = document.querySelector('#gameContainer .btn-gold');
    if (startBtn) startBtn.disabled = false;
    var msg = document.getElementById('mMsg');
    msg.textContent = 'BOOM! Mine hit! Lost ' + fmt(window._mBet); msg.className = 'result-msg lose';
    addHist(document.getElementById('mHist'), 'Hit mine after ' + window._mRev + ' gems | -' + fmt(window._mBet));
  } else {
    cell.textContent = '\uD83D\uDC8E'; cell.classList.add('gem');
    window._mRev++;
    document.getElementById('mRev').textContent = String(window._mRev);
    var m = mineMultCalc(window._mRev, window._mNumM);
    document.getElementById('mMult').textContent = fmtX(m);
    document.getElementById('mCash').disabled = false;
    if (window._mRev >= 25 - window._mNumM) { mineCash(null, document.getElementById('mCash')); }
  }
}

function mineCash(startBtn, cashBtn) {
  if (window._mState !== 'playing' || window._mRev === 0) return;
  window._mState = 'over';
  var m = mineMultCalc(window._mRev, window._mNumM);
  var payout = window._mBet * m; balance += payout; updateBal();
  if (cashBtn) cashBtn.disabled = true;
  document.getElementById('mSel').disabled = false;
  var sb = startBtn || document.querySelector('#gameContainer .btn-gold');
  if (sb) sb.disabled = false;
  var msg = document.getElementById('mMsg');
  msg.textContent = 'Cashed out! ' + window._mRev + ' gems @ ' + fmtX(m) + '  +' + fmt(payout); msg.className = 'result-msg win';
  addHist(document.getElementById('mHist'), window._mRev + ' gems | ' + fmtX(m) + ' | +' + fmt(payout));
}

// ─── 4. PLINKO ───────────────────────────────────────────────────────────────
var PK_MULTS = [10, 5, 3, 1.5, 0.5, 0.2, 0.5, 1.5, 3, 5, 10];
var PK_ROWS = 10;

function renderPlinko(container, title) {
  var box = gameBox(container, title, 'Ball bounces through pegs \xb7 Land in multiplier slots!');
  var screen = el('div', 'plinko-screen');
  var canvas = el('div', 'plinko-canvas'); canvas.id = 'pkCanvas'; screen.appendChild(canvas);
  var slotsRow = el('div', 'plinko-slots');
  PK_MULTS.forEach(function(m, i){
    var s = el('div', 'ps', m + 'x'); s.id = 'ps' + i; slotsRow.appendChild(s);
  });
  screen.appendChild(slotsRow); box.appendChild(screen);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('pkBet', '10'));
  box.appendChild(betRow);

  var dropBtn = btn('btn-gold', '\u26BD Drop Ball', null);
  dropBtn.onclick = function(){ plinkoDrop(dropBtn); };
  box.appendChild(dropBtn);
  var msg = el('div', 'result-msg'); msg.id = 'pkMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'pkHist'; box.appendChild(hist);
  // Draw pegs after DOM is ready
  setTimeout(function(){ plinkoDrawPegs(canvas); }, 0);
}

function plinkoDrawPegs(canvas) {
  if (!canvas) canvas = document.getElementById('pkCanvas');
  while (canvas.firstChild) canvas.removeChild(canvas.firstChild);
  var w = canvas.offsetWidth || 280;
  for (var r = 0; r < PK_ROWS; r++) {
    var pegs = r + 3;
    for (var p = 0; p < pegs; p++) {
      var peg = el('div', 'pp');
      var cw = w / (pegs + 1);
      peg.style.left = (cw * (p + 1) - 4) + 'px';
      peg.style.top = ((r + 1) * (240 / (PK_ROWS + 1)) - 4) + 'px';
      canvas.appendChild(peg);
    }
  }
}

async function plinkoDrop(dropBtn) {
  var bet = getBet('pkBet');
  if (!checkBal(bet, 'pkMsg')) return;
  balance -= bet; updateBal();
  dropBtn.disabled = true; dropBtn.textContent = 'Dropping...';
  PK_MULTS.forEach(function(_, i){ var s = document.getElementById('ps'+i); if(s) s.classList.remove('hit'); });
  var canvas = document.getElementById('pkCanvas');
  var w = canvas ? (canvas.offsetWidth || 280) : 280;
  var col = 0;
  var path = [];
  for (var r = 0; r < PK_ROWS; r++) { col += rndInt(0, 1); path.push(col); }
  var ball = el('div', 'pb');
  ball.style.left = (w / 2 - 7) + 'px'; ball.style.top = '0px';
  if (canvas) canvas.appendChild(ball);
  for (var s2 = 0; s2 < PK_ROWS; s2++) {
    await delay(100);
    var pegs = s2 + 3; var cw2 = w / (pegs + 1);
    var tx = cw2 * (path[s2] + 1) + (path[s2] > 0 ? 8 : -8) - 7;
    var ty = (s2 + 1) * (240 / (PK_ROWS + 1));
    ball.style.transition = 'left .1s ease, top .1s ease';
    ball.style.left = Math.max(0, Math.min(w - 14, tx)) + 'px';
    ball.style.top = ty + 'px';
  }
  await delay(150);
  if (canvas && ball.parentNode === canvas) canvas.removeChild(ball);
  var slotIdx = Math.min(PK_MULTS.length - 1, Math.max(0, col));
  var slot = document.getElementById('ps' + slotIdx); if (slot) slot.classList.add('hit');
  var mult = PK_MULTS[slotIdx];
  var payout = bet * mult; balance += payout; updateBal();
  var msg = document.getElementById('pkMsg');
  msg.textContent = 'Landed ' + mult + 'x!  Payout: ' + fmt(payout);
  msg.className = 'result-msg ' + (mult >= 1 ? 'win' : 'lose');
  addHist(document.getElementById('pkHist'), mult + 'x | Bet:' + fmt(bet) + ' | Payout:' + fmt(payout));
  dropBtn.disabled = false; dropBtn.textContent = '\u26BD Drop Ball';
}

// ─── 5. BLACKJACK ────────────────────────────────────────────────────────────
var BJ_SUITS2 = ['\u2665','\u2666','\u2663','\u2660'];
var BJ_RANKS2 = ['A','2','3','4','5','6','7','8','9','10','J','Q','K'];

function renderBlackjack(container, title) {
  var box = gameBox(container, title, 'Standard Blackjack \xb7 Dealer stands on 17 \xb7 Blackjack pays 3:2');
  var cardsArea = el('div', 'cards-area');

  var dLabel = el('div', 'cards-label', 'Dealer ');
  var dScore = el('span', 'bj-score'); dScore.id = 'bjDS'; dLabel.appendChild(dScore);
  var dRow = el('div', 'cards-row'); dRow.id = 'bjDC';
  cardsArea.appendChild(dLabel); cardsArea.appendChild(dRow);

  var pLabel = el('div', 'cards-label', 'You '); pLabel.style.marginTop = '10px';
  var pScore = el('span', 'bj-score'); pScore.id = 'bjPS'; pLabel.appendChild(pScore);
  var pRow = el('div', 'cards-row'); pRow.id = 'bjPC';
  cardsArea.appendChild(pLabel); cardsArea.appendChild(pRow);
  box.appendChild(cardsArea);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('bjBet', '10'));
  box.appendChild(betRow);

  var bjBtns = el('div', 'bj-buttons');
  var dealBtn = btn('btn-gold', 'Deal', null);
  var hitBtn  = btn('btn-green', 'Hit', null); hitBtn.disabled = true;
  var standBtn = btn('btn-red', 'Stand', null); standBtn.disabled = true;
  dealBtn.onclick = function(){ bjDeal(dealBtn, hitBtn, standBtn); };
  hitBtn.onclick  = function(){ bjHit(dealBtn, hitBtn, standBtn); };
  standBtn.onclick = function(){ bjResolve(dealBtn, hitBtn, standBtn); };
  bjBtns.appendChild(dealBtn); bjBtns.appendChild(hitBtn); bjBtns.appendChild(standBtn);
  box.appendChild(bjBtns);

  var msg = el('div', 'result-msg'); msg.id = 'bjMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'bjHist'; box.appendChild(hist);
  window._bjDeck = []; window._bjPlayer = []; window._bjDealer = [];
}

function bjNewDeck() {
  var deck = [];
  for (var s = 0; s < 4; s++) for (var r = 0; r < 13; r++) deck.push({r:r, s:s});
  for (var i = deck.length - 1; i > 0; i--) { var j = rndInt(0, i); var t = deck[i]; deck[i] = deck[j]; deck[j] = t; }
  return deck;
}

function bjVal(rank) { return rank === 0 ? 11 : rank >= 10 ? 10 : rank + 1; }
function bjHand(hand) {
  var v = 0; var aces = 0;
  hand.forEach(function(c){ v += bjVal(c.r); if (c.r === 0) aces++; });
  while (v > 21 && aces > 0) { v -= 10; aces--; }
  return v;
}

function bjMakeCard(c) {
  var isRed = c.s < 2;
  var card = el('div', 'card ' + (isRed ? 'red' : 'black'), BJ_RANKS2[c.r]);
  var suit = el('span', 'card-suit', BJ_SUITS2[c.s]);
  card.appendChild(suit); return card;
}
function bjMakeFaceDown() { return el('div', 'card face-down', ''); }

function bjUpdateDisplay(hideDealer) {
  var dc = document.getElementById('bjDC'); var pc = document.getElementById('bjPC');
  while (dc.firstChild) dc.removeChild(dc.firstChild);
  while (pc.firstChild) pc.removeChild(pc.firstChild);
  if (hideDealer) {
    dc.appendChild(bjMakeCard(window._bjDealer[0])); dc.appendChild(bjMakeFaceDown());
    document.getElementById('bjDS').textContent = String(bjVal(window._bjDealer[0].r));
  } else {
    window._bjDealer.forEach(function(c){ dc.appendChild(bjMakeCard(c)); });
    document.getElementById('bjDS').textContent = String(bjHand(window._bjDealer));
  }
  window._bjPlayer.forEach(function(c){ pc.appendChild(bjMakeCard(c)); });
  document.getElementById('bjPS').textContent = String(bjHand(window._bjPlayer));
}

function bjDeal(dealBtn, hitBtn, standBtn) {
  var bet = getBet('bjBet'); if (!checkBal(bet, 'bjMsg')) return;
  balance -= bet; updateBal(); window._bjBet = bet;
  window._bjDeck = bjNewDeck();
  window._bjPlayer = [window._bjDeck.pop(), window._bjDeck.pop()];
  window._bjDealer = [window._bjDeck.pop(), window._bjDeck.pop()];
  bjUpdateDisplay(true);
  dealBtn.disabled = true; hitBtn.disabled = false; standBtn.disabled = false;
  document.getElementById('bjMsg').textContent = '';
  if (bjHand(window._bjPlayer) === 21) bjResolve(dealBtn, hitBtn, standBtn);
}

function bjHit(dealBtn, hitBtn, standBtn) {
  window._bjPlayer.push(window._bjDeck.pop()); bjUpdateDisplay(true);
  if (bjHand(window._bjPlayer) >= 21) bjResolve(dealBtn, hitBtn, standBtn);
}

function bjResolve(dealBtn, hitBtn, standBtn) {
  hitBtn.disabled = true; standBtn.disabled = true;
  while (bjHand(window._bjDealer) < 17) window._bjDealer.push(window._bjDeck.pop());
  bjUpdateDisplay(false);
  var p = bjHand(window._bjPlayer); var d = bjHand(window._bjDealer);
  var msg = document.getElementById('bjMsg'); var payout = 0; var label = '';
  if (p > 21) { label = 'Bust! You lose.'; msg.className = 'result-msg lose'; }
  else if (d > 21 || p > d) {
    var bj = p === 21 && window._bjPlayer.length === 2;
    payout = bj ? window._bjBet * 2.5 : window._bjBet * 2;
    label = bj ? 'BLACKJACK! +' + fmt(payout) : 'WIN! +' + fmt(payout);
    balance += payout; updateBal(); msg.className = 'result-msg win';
  } else if (p === d) {
    payout = window._bjBet; label = 'Push. Bet returned.'; balance += payout; updateBal();
    msg.className = 'result-msg'; msg.style.color = 'var(--dim)';
  } else { label = 'Dealer wins. -' + fmt(window._bjBet); msg.className = 'result-msg lose'; }
  msg.textContent = label;
  addHist(document.getElementById('bjHist'), 'You:' + p + ' Dealer:' + d + ' | ' + label);
  dealBtn.disabled = false;
}

// ─── 6. ROULETTE ─────────────────────────────────────────────────────────────
var RR_RED = [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36];

function renderRoulette(container, title) {
  var box = gameBox(container, title, 'Pick a number (36x) or color (2x) \xb7 Zero is green (house edge)');
  var wheel = el('div', 'rw', '\uD83C\uDFB0'); wheel.id = 'rWheel'; box.appendChild(wheel);
  var resNum = el('div', null, '\u2014'); resNum.id = 'rResult'; resNum.style.cssText = 'font-family:Space Grotesk;font-size:2rem;font-weight:700;color:var(--gold);margin:8px 0';
  box.appendChild(resNum);

  // Number grid
  var grid = el('div', 'rn-grid');
  var z = el('div', 'rn rg', '0'); z.id = 'rn0'; z.onclick = function(){ rnSel(0); }; grid.appendChild(z);
  for (var n = 1; n <= 36; n++) {
    var cls2 = RR_RED.indexOf(n) !== -1 ? 'rr' : 'rb';
    var d = el('div', 'rn ' + cls2, String(n)); d.id = 'rn' + n;
    d.onclick = (function(num){ return function(){ rnSel(num); }; })(n);
    grid.appendChild(d);
  }
  box.appendChild(grid);

  var betRow2 = el('div', 'r-bets');
  var cRed = el('div', 'choice', '\uD83D\uDD34 Red (2x)'); cRed.onclick = function(){ rSetCol('red', cRed, cBlk, cNone); };
  var cBlk = el('div', 'choice', '\u26AB Black (2x)'); cBlk.onclick = function(){ rSetCol('black', cRed, cBlk, cNone); };
  var cNone = el('div', 'choice', 'Number Only'); cNone.style.fontSize = '0.75rem';
  cNone.onclick = function(){ rSetCol('none', cRed, cBlk, cNone); };
  betRow2.appendChild(cRed); betRow2.appendChild(cBlk); betRow2.appendChild(cNone); box.appendChild(betRow2);

  var betRow3 = el('div', 'bet-row');
  betRow3.appendChild(el('span', null, 'Bet $')); betRow3.appendChild(numInput('rBet', '10'));
  box.appendChild(betRow3);

  var spinBtn = btn('btn-gold', '\uD83C\uDFB0 Spin!', null);
  spinBtn.onclick = function(){ rSpin(spinBtn); };
  box.appendChild(spinBtn);
  var msg = el('div', 'result-msg'); msg.id = 'rMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'rHist'; box.appendChild(hist);
  window._rNum = -1; window._rColor = 'none';
}

function rnSel(n) {
  document.querySelectorAll('.rn').forEach(function(x){ x.classList.remove('sel'); });
  var el2 = document.getElementById('rn'+n); if (el2) el2.classList.add('sel');
  window._rNum = n;
}

function rSetCol(c, cRed, cBlk, cNone) {
  window._rColor = c;
  cRed.classList.toggle('active', c==='red');
  cBlk.classList.toggle('active', c==='black');
  cNone.classList.toggle('active', c==='none');
}

async function rSpin(spinBtn) {
  if (window._rNum === -1 && window._rColor === 'none') {
    var m = document.getElementById('rMsg'); m.textContent = 'Pick a number or color first!'; m.className = 'result-msg lose'; return;
  }
  var bet = getBet('rBet'); if (!checkBal(bet, 'rMsg')) return;
  balance -= bet; updateBal();
  spinBtn.disabled = true; spinBtn.textContent = 'Spinning...';
  var wheel = document.getElementById('rWheel');
  wheel.style.transform = 'rotate(' + (rndInt(5,12)*360 + rndInt(0,359)) + 'deg)';
  await delay(3100);
  var result = rndInt(0, 36);
  document.getElementById('rResult').textContent = String(result);
  var isRed = RR_RED.indexOf(result) !== -1;
  var isBlk = result !== 0 && !isRed;
  var payout = 0; var parts = [];
  if (window._rNum !== -1 && window._rNum === result) { payout += bet * 36; parts.push('Number! +' + fmt(bet*36)); }
  if (window._rColor === 'red' && isRed) { payout += bet * 2; parts.push('Red! +' + fmt(bet*2)); }
  if (window._rColor === 'black' && isBlk) { payout += bet * 2; parts.push('Black! +' + fmt(bet*2)); }
  var msg = document.getElementById('rMsg');
  var color = result === 0 ? ' (Green)' : isRed ? ' (Red)' : ' (Black)';
  if (payout > 0) {
    balance += payout; updateBal();
    msg.textContent = 'Result: ' + result + color + ' | ' + parts.join(', ');
    msg.className = 'result-msg win';
  } else {
    msg.textContent = 'Result: ' + result + color + ' | Lost ' + fmt(bet);
    msg.className = 'result-msg lose';
  }
  addHist(document.getElementById('rHist'), 'Result:' + result + color + ' | ' + (payout > 0 ? '+'+fmt(payout) : '-'+fmt(bet)));
  spinBtn.disabled = false; spinBtn.textContent = '\uD83C\uDFB0 Spin!';
}

// ─── 7. HI-LO ────────────────────────────────────────────────────────────────
function renderHiLo(container, title) {
  var box = gameBox(container, title, 'Guess Higher or Lower \xb7 Build a streak for bigger multipliers!');

  var infoRow = el('div', 'hilo-info');
  infoRow.appendChild(document.createTextNode('Streak: '));
  var streakSpan = el('strong', null, '0'); streakSpan.id = 'hlStreak'; infoRow.appendChild(streakSpan);
  infoRow.appendChild(document.createTextNode('\u00a0\u00a0Multiplier: '));
  var multSpan = el('span', 'hilo-mult', '1.00x'); multSpan.id = 'hlMult'; infoRow.appendChild(multSpan);
  box.appendChild(infoRow);

  var cardArea = el('div', 'hilo-card-area');
  var curCard = el('div', 'big-card black', '?'); curCard.id = 'hlCur';
  var arrow = el('div', null, '\u2192'); arrow.style.cssText = 'font-size:2rem;color:var(--dim)';
  var nxtCard = el('div', 'big-card fd'); nxtCard.id = 'hlNxt';
  cardArea.appendChild(curCard); cardArea.appendChild(arrow); cardArea.appendChild(nxtCard);
  box.appendChild(cardArea);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('hlBet', '10'));
  box.appendChild(betRow);

  var btnRow = el('div', 'bet-row');
  var dealBtn = btn('btn-gold', 'Deal Card', null);
  var hiBtn = btn('btn-green', '\u2B06 Higher', null); hiBtn.disabled = true;
  var loBtn = btn('btn-red', '\u2B07 Lower', null); loBtn.disabled = true;
  var cashBtn = btn('btn-outline', 'Cash Out', null); cashBtn.disabled = true;
  dealBtn.onclick = function(){ hlStart(dealBtn, hiBtn, loBtn, cashBtn); };
  hiBtn.onclick = function(){ hlGuess('high', dealBtn, hiBtn, loBtn, cashBtn); };
  loBtn.onclick = function(){ hlGuess('low', dealBtn, hiBtn, loBtn, cashBtn); };
  cashBtn.onclick = function(){ hlCash(dealBtn, hiBtn, loBtn, cashBtn); };
  btnRow.appendChild(dealBtn); btnRow.appendChild(hiBtn); btnRow.appendChild(loBtn); btnRow.appendChild(cashBtn);
  box.appendChild(btnRow);

  var msg = el('div', 'result-msg'); msg.id = 'hlMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'hlHist'; box.appendChild(hist);
  window._hlStreak = 0; window._hlCur = null; window._hlState = 'idle';
}

function hlRenderCard(rank, suit, el2) {
  var isRed = suit < 2;
  el2.className = 'big-card ' + (isRed ? 'red' : 'black');
  while (el2.firstChild) el2.removeChild(el2.firstChild);
  var ss = el('span', 'small-suit', BJ_SUITS2[suit]); el2.appendChild(ss);
  el2.appendChild(document.createTextNode(BJ_RANKS2[rank]));
}

function hlStart(dealBtn, hiBtn, loBtn, cashBtn) {
  var bet = getBet('hlBet'); if (!checkBal(bet, 'hlMsg')) return;
  balance -= bet; updateBal(); window._hlBet = bet; window._hlStreak = 0;
  var rank = rndInt(0, 12); var suit = rndInt(0, 3);
  window._hlCur = {rank:rank, suit:suit};
  hlRenderCard(rank, suit, document.getElementById('hlCur'));
  var nxt = document.getElementById('hlNxt'); nxt.className = 'big-card fd';
  while (nxt.firstChild) nxt.removeChild(nxt.firstChild);
  document.getElementById('hlStreak').textContent = '0';
  document.getElementById('hlMult').textContent = '1.00x';
  dealBtn.disabled = true; hiBtn.disabled = false; loBtn.disabled = false; cashBtn.disabled = true;
  document.getElementById('hlMsg').textContent = '';
  window._hlState = 'playing';
}

async function hlGuess(guess, dealBtn, hiBtn, loBtn, cashBtn) {
  if (window._hlState !== 'playing') return;
  hiBtn.disabled = true; loBtn.disabled = true; cashBtn.disabled = true;
  var nr = rndInt(0, 12); var ns = rndInt(0, 3);
  hlRenderCard(nr, ns, document.getElementById('hlNxt'));
  await delay(400);
  var cur = window._hlCur.rank;
  var won = (guess === 'high' && nr > cur) || (guess === 'low' && nr < cur);
  var tie = nr === cur;
  var msg = document.getElementById('hlMsg');
  if (tie) {
    msg.textContent = 'Tie! Try again.'; msg.className = 'result-msg';
    hiBtn.disabled = false; loBtn.disabled = false;
    if (window._hlStreak > 0) cashBtn.disabled = false;
    window._hlCur = {rank:nr, suit:ns};
    hlRenderCard(nr, ns, document.getElementById('hlCur'));
    var nxt2 = document.getElementById('hlNxt'); nxt2.className = 'big-card fd';
    while (nxt2.firstChild) nxt2.removeChild(nxt2.firstChild);
    return;
  }
  if (won) {
    window._hlStreak++;
    var m = parseFloat((1 + window._hlStreak * 0.5).toFixed(2));
    document.getElementById('hlStreak').textContent = String(window._hlStreak);
    document.getElementById('hlMult').textContent = fmtX(m);
    msg.textContent = 'Correct! Streak x' + window._hlStreak + '  ' + fmtX(m); msg.className = 'result-msg win';
    window._hlCur = {rank:nr, suit:ns};
    hlRenderCard(nr, ns, document.getElementById('hlCur'));
    var nxt3 = document.getElementById('hlNxt'); nxt3.className = 'big-card fd';
    while (nxt3.firstChild) nxt3.removeChild(nxt3.firstChild);
    hiBtn.disabled = false; loBtn.disabled = false; cashBtn.disabled = false;
  } else {
    window._hlState = 'over';
    msg.textContent = 'Wrong! Lost ' + fmt(window._hlBet); msg.className = 'result-msg lose';
    addHist(document.getElementById('hlHist'), 'Streak:' + window._hlStreak + ' | Wrong | -' + fmt(window._hlBet));
    dealBtn.disabled = false;
  }
}

function hlCash(dealBtn, hiBtn, loBtn, cashBtn) {
  if (window._hlState !== 'playing' || window._hlStreak === 0) return;
  window._hlState = 'over';
  var m = parseFloat((1 + window._hlStreak * 0.5).toFixed(2));
  var payout = window._hlBet * m; balance += payout; updateBal();
  hiBtn.disabled = true; loBtn.disabled = true; cashBtn.disabled = true; dealBtn.disabled = false;
  var msg = document.getElementById('hlMsg');
  msg.textContent = 'Cashed! Streak x' + window._hlStreak + ' @ ' + fmtX(m) + '  +' + fmt(payout); msg.className = 'result-msg win';
  addHist(document.getElementById('hlHist'), 'Streak:' + window._hlStreak + ' | ' + fmtX(m) + ' | +' + fmt(payout));
}

// ─── 8. TOWER ────────────────────────────────────────────────────────────────
var TW_ROWS = 8;
var TW_COLS = 3;
var TW_MULTS = [1.3, 1.7, 2.2, 2.9, 3.8, 5.0, 6.5, 8.5];

function renderTower(container, title) {
  var box = gameBox(container, title, '3 columns, 8 rows \xb7 One column per row is death \xb7 Cash out any time!');

  var wrap = el('div', 'tower-wrap');
  // Multiplier labels column
  var mCol = el('div', 'tower-mult-col');
  for (var r = TW_ROWS - 1; r >= 0; r--) {
    var ml = el('div', 'tml', TW_MULTS[r] + 'x'); mCol.appendChild(ml);
  }
  wrap.appendChild(mCol);
  // Game columns
  for (var c = 0; c < TW_COLS; c++) {
    var col = el('div', 'tower-col');
    for (var rr = TW_ROWS - 1; rr >= 0; rr--) {
      var cell = el('div', 'tc', '\u2B1C');
      cell.id = 'tc_' + rr + '_' + c;
      cell.onclick = (function(row, colIdx){ return function(){ towerPick(row, colIdx); }; })(rr, c);
      col.appendChild(cell);
    }
    wrap.appendChild(col);
  }
  box.appendChild(wrap);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('twBet', '10'));
  box.appendChild(betRow);

  var btnRow = el('div', 'bet-row');
  var startBtn = btn('btn-gold', 'Start Climb', null);
  var cashBtn = btn('btn-green', 'Cash Out', null); cashBtn.disabled = true;
  startBtn.onclick = function(){ towerStart(startBtn, cashBtn); };
  cashBtn.onclick = function(){ towerCash(startBtn, cashBtn); };
  btnRow.appendChild(startBtn); btnRow.appendChild(cashBtn); box.appendChild(btnRow);

  var msg = el('div', 'result-msg'); msg.id = 'twMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'twHist'; box.appendChild(hist);
  window._twState = 'idle'; window._twRow = 0; window._twDeaths = [];
}

function twResetCells() {
  for (var r = 0; r < TW_ROWS; r++) for (var c = 0; c < TW_COLS; c++) {
    var cell = document.getElementById('tc_'+r+'_'+c);
    if (cell) { cell.className = 'tc'; cell.textContent = '\u2B1C'; }
  }
}

function twSetActiveRow(row) {
  for (var r = 0; r < TW_ROWS; r++) for (var c = 0; c < TW_COLS; c++) {
    var cell = document.getElementById('tc_'+r+'_'+c);
    if (!cell) continue;
    if (r === row && window._twState === 'playing' && !cell.classList.contains('safe') && !cell.classList.contains('passed')) {
      cell.classList.add('ar');
    } else { cell.classList.remove('ar'); }
  }
}

function towerStart(startBtn, cashBtn) {
  var bet = getBet('twBet'); if (!checkBal(bet, 'twMsg')) return;
  balance -= bet; updateBal(); window._twBet = bet; window._twRow = 0; window._twState = 'playing';
  window._twDeaths = [];
  for (var r = 0; r < TW_ROWS; r++) window._twDeaths.push(rndInt(0, TW_COLS - 1));
  twResetCells(); twSetActiveRow(0);
  startBtn.disabled = true; cashBtn.disabled = true;
  var msg = document.getElementById('twMsg');
  msg.textContent = 'Row 1 of ' + TW_ROWS + ' \u2014 pick a column!'; msg.className = 'result-msg';
}

function towerPick(row, col) {
  if (window._twState !== 'playing' || row !== window._twRow) return;
  var death = window._twDeaths[row];
  var cell = document.getElementById('tc_'+row+'_'+col);
  var deathCell = document.getElementById('tc_'+row+'_'+death);
  if (col === death) {
    cell.className = 'tc dead'; cell.textContent = '\uD83D\uDC80';
    for (var c2 = 0; c2 < TW_COLS; c2++) {
      if (c2 !== death) { var sc = document.getElementById('tc_'+row+'_'+c2); sc.className = 'tc'; sc.textContent = '\u2705'; }
    }
    window._twState = 'over';
    var cashBtn2 = document.querySelector('#gameContainer .btn-green'); if (cashBtn2) cashBtn2.disabled = true;
    var startBtn2 = document.querySelector('#gameContainer .btn-gold'); if (startBtn2) startBtn2.disabled = false;
    var msg = document.getElementById('twMsg');
    msg.textContent = 'DEAD at row ' + (row+1) + '! Lost ' + fmt(window._twBet); msg.className = 'result-msg lose';
    addHist(document.getElementById('twHist'), 'Died row ' + (row+1) + ' | -' + fmt(window._twBet));
  } else {
    cell.className = 'tc safe'; cell.textContent = '\u2705';
    if (deathCell) { deathCell.className = 'tc passed'; deathCell.textContent = '\uD83D\uDC80'; }
    window._twRow++;
    if (window._twRow >= TW_ROWS) {
      var cashBtn3 = document.querySelector('#gameContainer .btn-green');
      towerCash(null, cashBtn3);
    } else {
      twSetActiveRow(window._twRow);
      var cashBtn4 = document.querySelector('#gameContainer .btn-green'); if (cashBtn4) cashBtn4.disabled = false;
      var msg2 = document.getElementById('twMsg');
      msg2.textContent = 'Row ' + (window._twRow+1) + ' of ' + TW_ROWS + ' | Mult: ' + fmtX(TW_MULTS[window._twRow-1]);
      msg2.className = 'result-msg win';
    }
  }
}

function towerCash(startBtn, cashBtn) {
  if (window._twState !== 'playing' || window._twRow === 0) return;
  window._twState = 'over';
  var m = TW_MULTS[Math.min(window._twRow - 1, TW_MULTS.length - 1)];
  var payout = window._twBet * m; balance += payout; updateBal();
  if (cashBtn) cashBtn.disabled = true;
  var sb = startBtn || document.querySelector('#gameContainer .btn-gold'); if (sb) sb.disabled = false;
  var msg = document.getElementById('twMsg');
  msg.textContent = 'Cashed at row ' + window._twRow + ' @ ' + fmtX(m) + '  +' + fmt(payout); msg.className = 'result-msg win';
  addHist(document.getElementById('twHist'), 'Row ' + window._twRow + ' | ' + fmtX(m) + ' | +' + fmt(payout));
}

// ─── 9. SLOTS ────────────────────────────────────────────────────────────────
var SL_SYM = ['\uD83C\uDF4B','\uD83C\uDF47','\uD83C\uDF53','\uD83D\uDC8E','\u2764\uFE0F','\uD83D\uDD11','\uD83C\uDFB0','\u2B50'];
var SL_MULT = [0,0,0,0,0,8,15,50]; // per symbol: 3-of-a-kind multiplier

function renderSlots(container, title) {
  var box = gameBox(container, title, '3-reel slot \xb7 Match all 3 to win \xb7 \u2B50\u2B50\u2B50 = Jackpot!');
  var reels = el('div', 'slots-display');
  for (var i = 0; i < 3; i++) {
    var r = el('div', 'reel'); r.id = 'reel' + i;
    var s = el('span', null, '\uD83C\uDFB0'); r.appendChild(s); reels.appendChild(r);
  }
  box.appendChild(reels);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('slBet', '10'));
  box.appendChild(betRow);

  var spinBtn = btn('btn-gold', '\uD83C\uDFB0 Spin!', null);
  spinBtn.onclick = function(){ slotSpin(spinBtn); };
  box.appendChild(spinBtn);
  var msg = el('div', 'result-msg'); msg.id = 'slMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'slHist'; box.appendChild(hist);
}

async function slotSpin(spinBtn) {
  var bet = getBet('slBet'); if (!checkBal(bet, 'slMsg')) return;
  balance -= bet; updateBal();
  spinBtn.disabled = true; spinBtn.textContent = 'Spinning...';
  for (var i = 0; i < 3; i++) { var r = document.getElementById('reel'+i); r.className = 'reel spinning'; }
  await delay(800);
  var results = [rndInt(0, SL_SYM.length-1), rndInt(0, SL_SYM.length-1), rndInt(0, SL_SYM.length-1)];
  for (var j = 0; j < 3; j++) {
    var reel = document.getElementById('reel'+j); reel.className = 'reel';
    while (reel.firstChild) reel.removeChild(reel.firstChild);
    var span = el('span', null, SL_SYM[results[j]]); reel.appendChild(span);
  }
  var payout = 0; var label = '';
  if (results[0] === results[1] && results[1] === results[2]) {
    var mult = SL_MULT[results[0]] || 3;
    payout = bet * mult; balance += payout; updateBal();
    label = results[0] === 7 ? 'JACKPOT! \u2B50\u2B50\u2B50 +' + fmt(payout) : '3 of a kind! +' + fmt(payout);
    document.getElementById('slMsg').textContent = label;
    document.getElementById('slMsg').className = 'result-msg win';
  } else if (results[0]===results[1] || results[1]===results[2] || results[0]===results[2]) {
    payout = bet * 0.5; balance += payout; updateBal();
    label = '2 of a kind! +' + fmt(payout);
    document.getElementById('slMsg').textContent = label;
    document.getElementById('slMsg').className = 'result-msg win';
  } else {
    label = 'No match. -' + fmt(bet);
    document.getElementById('slMsg').textContent = label;
    document.getElementById('slMsg').className = 'result-msg lose';
  }
  addHist(document.getElementById('slHist'), results.map(function(r2){ return SL_SYM[r2]; }).join(' ') + ' | ' + label);
  spinBtn.disabled = false; spinBtn.textContent = '\uD83C\uDFB0 Spin!';
}

// ─── 10. COIN FLIP ───────────────────────────────────────────────────────────
function renderCoinFlip(container, title) {
  var box = gameBox(container, title, 'Heads or Tails \xb7 2x payout \xb7 Provably fair coin flip');
  var coinEl = el('div', 'coin', '\uD83E\uFA99'); coinEl.id = 'coinEl'; box.appendChild(coinEl);

  var betRow = el('div', 'bet-row');
  betRow.appendChild(el('span', null, 'Bet $')); betRow.appendChild(numInput('cfBet', '10'));
  box.appendChild(betRow);

  var choiceRow = el('div', 'choice-row');
  var cH = el('div', 'choice active', '\uD83D\uDC51 Heads'); cH.id = 'cfH';
  var cT = el('div', 'choice', '\uD83E\uFA99 Tails'); cT.id = 'cfT';
  cH.onclick = function(){ window._cfSide='heads'; cH.classList.add('active'); cT.classList.remove('active'); };
  cT.onclick = function(){ window._cfSide='tails'; cT.classList.add('active'); cH.classList.remove('active'); };
  choiceRow.appendChild(cH); choiceRow.appendChild(cT); box.appendChild(choiceRow);

  var flipBtn = btn('btn-gold', 'Flip!', null);
  flipBtn.onclick = function(){ cfFlip(flipBtn, coinEl); };
  box.appendChild(flipBtn);
  var msg = el('div', 'result-msg'); msg.id = 'cfMsg'; box.appendChild(msg);
  var hist = el('div', 'history'); hist.id = 'cfHist'; box.appendChild(hist);
  window._cfSide = 'heads';
}

async function cfFlip(flipBtn, coinEl) {
  var bet = getBet('cfBet'); if (!checkBal(bet, 'cfMsg')) return;
  flipBtn.disabled = true; flipBtn.textContent = 'Flipping...';
  coinEl.className = 'coin flipping';
  balance -= bet; updateBal();
  await delay(900);
  coinEl.className = 'coin';
  var result = rndInt(0,1) === 0 ? 'heads' : 'tails';
  coinEl.textContent = result === 'heads' ? '\uD83D\uDC51' : '\uD83E\uFA99';
  var won = result === window._cfSide;
  var payout = won ? bet * 2 : 0;
  if (won) { balance += payout; updateBal(); }
  var msg = document.getElementById('cfMsg');
  var resLabel = result === 'heads' ? 'Heads!' : 'Tails!';
  msg.textContent = resLabel + ' | ' + (won ? 'WIN! +' + fmt(payout) : 'LOSE. -' + fmt(bet));
  msg.className = 'result-msg ' + (won ? 'win' : 'lose');
  addHist(document.getElementById('cfHist'), result + ' | ' + (won ? '+'+fmt(payout) : '-'+fmt(bet)));
  flipBtn.disabled = false; flipBtn.textContent = 'Flip!';
}
</script></body></html>`;
}
