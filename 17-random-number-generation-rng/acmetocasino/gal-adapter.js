// Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * GAL Adapter - Bridges existing casino games to the server-side architecture.
 *
 * This adapter intercepts localStorage and Math.random calls to route them
 * through the GAL (Game Aggregation Layer) API, enabling:
 * - Server-side RNG (CSPRNG via Python secrets module)
 * - Event-sourced wallet (balance from server, not localStorage)
 * - Server-side RTP control (from database, not localStorage)
 * - Audit trail (every RNG call logged with seed hash)
 * - Automatic bet/win recording when games change balance via localStorage
 *
 * Usage: Include this script BEFORE the game script in each HTML file.
 * <script src="/games/gal-adapter.js"></script>
 */

(function() {
  'use strict';

  const API_BASE = '/api/v2';
  const GAL_CONFIG = {
    // Will be populated after login/session init
    token: null,
    playerId: null,
    sessionId: null,
    gameSlug: null,
    balance: 10000,
    connected: false,
    // RNG buffer - pre-fetched random numbers from server
    rngBuffer: [],
    rngBufferSize: 100,
    rngFetching: false,
    // RTP from server
    serverRTP: null,
    // Stats
    totalBet: 0,
    totalWon: 0,
    roundsPlayed: 0,
    auditLog: [],
    // Bet tracking state
    _betDebounceTimer: null,
    _betDebounceMs: 80,
    _pendingBalanceWrite: null,
    _processingBet: false
  };

  // ========================================
  // JWT Helper
  // ========================================

  function parseJwtPayload(token) {
    try {
      var parts = token.split('.');
      if (parts.length !== 3) return null;
      var payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(atob(payload));
    } catch(e) { return null; }
  }

  // ========================================
  // Session Management
  // ========================================

  async function initSession(gameSlug) {
    GAL_CONFIG.gameSlug = gameSlug;

    // Try to get existing session from sessionStorage
    const saved = sessionStorage.getItem('gal_session');
    if (saved) {
      try {
        const data = JSON.parse(saved);
        GAL_CONFIG.token = data.token;
        GAL_CONFIG.playerId = data.playerId;
        // Ensure playerId is set by decoding JWT if missing
        if (!GAL_CONFIG.playerId && GAL_CONFIG.token) {
          var jwt = parseJwtPayload(GAL_CONFIG.token);
          if (jwt) GAL_CONFIG.playerId = jwt.sub;
        }
        GAL_CONFIG.sessionId = data.sessionId;
        GAL_CONFIG.balance = data.balance || 10000;
        GAL_CONFIG.connected = true;
      } catch(e) {}
    }

    // Auto-login as demo player if no session
    if (!GAL_CONFIG.token) {
      await autoLogin();
    }

    // Fetch server RTP for this game
    await fetchServerRTP(gameSlug);

    // Pre-fill RNG buffer
    await refillRNGBuffer();

    // Launch game session
    if (GAL_CONFIG.token && gameSlug) {
      await launchGameSession(gameSlug);
    }

    // Show connection indicator
    showConnectionStatus();

    return GAL_CONFIG;
  }

  // The demo password is supplied by the host page, never committed:
  //   <script>window.GAL_DEMO_PASSWORD = '...';</script>
  const DEMO_PASSWORD = (typeof window !== 'undefined' && window.GAL_DEMO_PASSWORD) || '';

  async function autoLogin() {
    if (!DEMO_PASSWORD) {
      console.warn('[GAL] set window.GAL_DEMO_PASSWORD to enable the demo auto-login');
      return null;
    }
    try {
      // Register a demo player or login existing
      const username = 'demo_' + Math.floor(Date.now() / 1000);
      const email = username + '@demo.acmetocasino.com';
      const password = DEMO_PASSWORD;

      // Try login first with a known demo account
      try {
        const loginRes = await fetch(API_BASE + '/auth/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({email: 'demo_player@demo.acmetocasino.com', password: DEMO_PASSWORD})
        });
        if (loginRes.ok) {
          const data = await loginRes.json();
          GAL_CONFIG.token = data.access_token;
          var jwt = parseJwtPayload(data.access_token);
          if (jwt) GAL_CONFIG.playerId = jwt.sub;
          GAL_CONFIG.connected = true;
          saveSession();
          await fetchBalance();
          return;
        }
      } catch(e) {}

      // Register new demo player
      const regRes = await fetch(API_BASE + '/players', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: username,
          email: email,
          password: password
        })
      });

      if (regRes.ok || regRes.status === 409) {
        const loginRes = await fetch(API_BASE + '/auth/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({email: email, password: password})
        });
        if (loginRes.ok) {
          const data = await loginRes.json();
          GAL_CONFIG.token = data.access_token;
          var jwt = parseJwtPayload(data.access_token);
          if (jwt) GAL_CONFIG.playerId = jwt.sub;
          GAL_CONFIG.connected = true;
          saveSession();

          // Auto-verify KYC for demo player
          await fetch(API_BASE + '/compliance/kyc/' + GAL_CONFIG.playerId + '/auto-verify', {
            method: 'POST',
            headers: authHeaders()
          });

          // Give initial deposit
          await fetch(API_BASE + '/wallet/' + GAL_CONFIG.playerId + '/transaction', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({event_type: 'DEPOSIT', amount: 10000, currency: 'USD'})
          });
          await fetchBalance();
        }
      }
    } catch(e) {
      console.warn('[GAL] Auto-login failed, running in offline mode:', e.message);
      GAL_CONFIG.connected = false;
    }
  }

  async function fetchBalance() {
    if (!GAL_CONFIG.token || !GAL_CONFIG.playerId) return;
    try {
      const res = await fetch(API_BASE + '/wallet/' + GAL_CONFIG.playerId + '/balance', {
        headers: authHeaders()
      });
      if (res.ok) {
        const data = await res.json();
        GAL_CONFIG.balance = parseFloat(data.balance) || 0;
      }
    } catch(e) {}
  }

  async function fetchServerRTP(gameSlug) {
    try {
      const res = await fetch(API_BASE + '/game-control/rtp');
      if (res.ok) {
        const configs = await res.json();
        const config = configs.find(c => c.game_slug === gameSlug);
        if (config) {
          GAL_CONFIG.serverRTP = config.target_rtp / 100;
        }
      }
    } catch(e) {}
  }

  async function launchGameSession(gameSlug) {
    if (!GAL_CONFIG.token) return;
    try {
      const res = await fetch(API_BASE + '/gal/launch', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          player_id: GAL_CONFIG.playerId,
          game_slug: gameSlug
        })
      });
      if (res.ok) {
        const data = await res.json();
        GAL_CONFIG.sessionId = data.session_id || data.id;
        saveSession();
      }
    } catch(e) {}
  }

  async function ensureSession() {
    if (GAL_CONFIG.sessionId) return true;
    if (!GAL_CONFIG.token || !GAL_CONFIG.gameSlug) return false;
    await launchGameSession(GAL_CONFIG.gameSlug);
    return !!GAL_CONFIG.sessionId;
  }

  // ========================================
  // Server-Side RNG
  // ========================================

  async function refillRNGBuffer() {
    if (GAL_CONFIG.rngFetching || !GAL_CONFIG.connected) return;
    GAL_CONFIG.rngFetching = true;
    try {
      // Fetch batch of random numbers from server
      const res = await fetch(API_BASE + '/gal/rng-batch', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({count: GAL_CONFIG.rngBufferSize})
      });
      if (res.ok) {
        const data = await res.json();
        if (data.numbers && Array.isArray(data.numbers)) {
          GAL_CONFIG.rngBuffer = GAL_CONFIG.rngBuffer.concat(data.numbers);
          if (data.audit) {
            GAL_CONFIG.auditLog.push(...(Array.isArray(data.audit) ? data.audit : [data.audit]));
          }
        }
      }
    } catch(e) {
      // Fallback: will use crypto.getRandomValues
    }
    GAL_CONFIG.rngFetching = false;
  }

  function getServerRandom() {
    if (GAL_CONFIG.rngBuffer.length > 0) {
      const val = GAL_CONFIG.rngBuffer.shift();
      // Refill when buffer is getting low
      if (GAL_CONFIG.rngBuffer.length < 20 && !GAL_CONFIG.rngFetching) {
        refillRNGBuffer();
      }
      return val;
    }
    // Fallback to crypto API (still better than Math.random)
    const arr = new Uint32Array(1);
    crypto.getRandomValues(arr);
    return arr[0] / 4294967296;
  }

  // ========================================
  // Place Bet via GAL API
  // ========================================

  async function placeBet(amount, gameSlug) {
    gameSlug = gameSlug || GAL_CONFIG.gameSlug;
    if (!GAL_CONFIG.connected || !GAL_CONFIG.token) {
      return {success: false, error: 'Not connected'};
    }
    try {
      const hasSession = await ensureSession();
      if (!hasSession) return {success: false, error: 'No session'};

      const res = await fetch(API_BASE + '/gal/bet', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          session_id: GAL_CONFIG.sessionId,
          player_id: GAL_CONFIG.playerId,
          game_slug: gameSlug,
          bet_amount: amount
        })
      });
      if (res.ok) {
        const data = await res.json();
        GAL_CONFIG.balance = parseFloat(data.new_balance) || (GAL_CONFIG.balance - amount + parseFloat(data.win_amount || 0));
        GAL_CONFIG.totalBet += amount;
        GAL_CONFIG.totalWon += parseFloat(data.win_amount || 0);
        GAL_CONFIG.roundsPlayed++;
        saveSession();
        return {
          success: true,
          win_amount: data.win_amount || 0,
          outcome: data.outcome || {},
          rng_seed_hash: data.rng_seed_hash,
          balance: GAL_CONFIG.balance
        };
      }
      return {success: false, error: 'API error'};
    } catch(e) {
      return {success: false, error: e.message};
    }
  }

  // ========================================
  // Balance Change Detection & Bet Recording
  // ========================================

  /**
   * Called when a game writes a new value to sim_balance via localStorage.setItem.
   * Detects bet (balance decrease) and records it on the server.
   * Uses debouncing to handle rapid balance updates within the same frame.
   */
  function onBalanceChange(newBalance) {
    var oldBalance = GAL_CONFIG.balance;
    var diff = oldBalance - newBalance;

    // Update local state immediately so the game sees the right value
    GAL_CONFIG.balance = newBalance;

    // Only record bets (balance decrease). Wins are handled server-side by the bet endpoint.
    if (diff <= 0.005) return; // Not a bet, or negligible rounding

    // Debounce: games may update balance multiple times per frame
    // Accumulate the total bet before sending
    if (GAL_CONFIG._betDebounceTimer) {
      clearTimeout(GAL_CONFIG._betDebounceTimer);
    }

    // Accumulate pending bet amount
    if (GAL_CONFIG._pendingBalanceWrite === null) {
      GAL_CONFIG._pendingBalanceWrite = diff;
    } else {
      GAL_CONFIG._pendingBalanceWrite += diff;
    }

    GAL_CONFIG._betDebounceTimer = setTimeout(function() {
      var betAmount = GAL_CONFIG._pendingBalanceWrite;
      GAL_CONFIG._pendingBalanceWrite = null;
      GAL_CONFIG._betDebounceTimer = null;

      if (betAmount > 0 && !GAL_CONFIG._processingBet) {
        recordBetFireAndForget(betAmount);
      }
    }, GAL_CONFIG._betDebounceMs);
  }

  /**
   * Fire-and-forget server bet recording.
   * Does NOT block the game UI. On success, syncs balance from server.
   * On failure, game continues with client-side balance (graceful degradation).
   */
  function recordBetFireAndForget(amount) {
    if (!GAL_CONFIG.connected || !GAL_CONFIG.token) return;

    GAL_CONFIG._processingBet = true;

    // Ensure we have a session, then place the bet
    var doRecord = function() {
      return ensureSession().then(function(hasSession) {
        if (!hasSession) {
          GAL_CONFIG._processingBet = false;
          return;
        }
        return fetch(API_BASE + '/gal/bet', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            session_id: GAL_CONFIG.sessionId,
            player_id: GAL_CONFIG.playerId,
            game_slug: GAL_CONFIG.gameSlug,
            bet_amount: amount
          })
        }).then(function(res) {
          if (res.ok) {
            return res.json().then(function(data) {
              // Sync balance from server (source of truth)
              if (data.new_balance !== undefined) {
                GAL_CONFIG.balance = parseFloat(data.new_balance);
              }
              GAL_CONFIG.totalBet += amount;
              GAL_CONFIG.totalWon += parseFloat(data.win_amount || 0);
              GAL_CONFIG.roundsPlayed++;
              saveSession();
              console.log('[GAL] Bet recorded: $' + amount.toFixed(2) +
                ', win: $' + (parseFloat(data.win_amount) || 0).toFixed(2) +
                ', balance: $' + GAL_CONFIG.balance.toFixed(2));
            });
          } else {
            console.warn('[GAL] Bet API returned status ' + res.status);
          }
        }).catch(function(e) {
          console.warn('[GAL] Bet recording failed (game continues):', e.message);
        }).then(function() {
          GAL_CONFIG._processingBet = false;
        });
      });
    };

    doRecord();
  }

  // ========================================
  // localStorage Proxy
  // ========================================

  const _origGetItem = localStorage.getItem.bind(localStorage);
  const _origSetItem = localStorage.setItem.bind(localStorage);

  // Intercept balance reads/writes
  const _proxyGetItem = function(key) {
    if (key === 'sim_balance' && GAL_CONFIG.connected) {
      return String(GAL_CONFIG.balance.toFixed(2));
    }
    // Intercept RTP reads - return server RTP
    if (key.startsWith('rtp_') && GAL_CONFIG.serverRTP !== null) {
      return String(GAL_CONFIG.serverRTP * 100);
    }
    return _origGetItem(key);
  };

  const _proxySetItem = function(key, value) {
    if (key === 'sim_balance' && GAL_CONFIG.connected) {
      // Don't write to localStorage - balance is server-side
      // Detect bet/win from the balance change and record on server
      var newBalance = parseFloat(value) || 0;
      onBalanceChange(newBalance);
      return;
    }
    if (key.startsWith('rtp_') && GAL_CONFIG.connected) {
      // RTP is server-side, ignore client writes
      return;
    }
    return _origSetItem(key, value);
  };

  // Override localStorage methods
  localStorage.getItem = _proxyGetItem;
  localStorage.setItem = _proxySetItem;

  // ========================================
  // Math.random Override
  // ========================================

  const _origMathRandom = Math.random;

  // Replace Math.random with server-side RNG
  Math.random = function() {
    if (GAL_CONFIG.connected && GAL_CONFIG.rngBuffer.length > 0) {
      return getServerRandom();
    }
    // Fallback to crypto if connected but buffer empty
    if (GAL_CONFIG.connected) {
      const arr = new Uint32Array(1);
      crypto.getRandomValues(arr);
      return arr[0] / 4294967296;
    }
    // Offline: use original
    return _origMathRandom();
  };

  // ========================================
  // UI: Connection Status Indicator
  // ========================================

  function showConnectionStatus() {
    const indicator = document.createElement('div');
    indicator.id = 'gal-status';
    indicator.style.cssText = 'position:fixed;top:8px;left:8px;z-index:99999;display:flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:11px;font-family:monospace;backdrop-filter:blur(8px);transition:all 0.3s;cursor:pointer;';

    function update() {
      if (GAL_CONFIG.connected) {
        indicator.style.background = 'rgba(105,240,174,0.15)';
        indicator.style.border = '1px solid rgba(105,240,174,0.3)';
        indicator.style.color = '#69f0ae';
        indicator.innerHTML = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#69f0ae;animation:pulse 2s infinite"></span> GAL Connected | RNG: Server-Side | Balance: $' + GAL_CONFIG.balance.toFixed(2);
      } else {
        indicator.style.background = 'rgba(255,82,82,0.15)';
        indicator.style.border = '1px solid rgba(255,82,82,0.3)';
        indicator.style.color = '#ff5252';
        indicator.innerHTML = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#ff5252"></span> GAL Offline | RNG: Client Fallback | Balance: $' + GAL_CONFIG.balance.toFixed(2);
      }
    }

    // Add pulse animation
    const style = document.createElement('style');
    style.textContent = '@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}';
    document.head.appendChild(style);

    update();
    setInterval(update, 3000);

    indicator.addEventListener('click', () => {
      showAuditPanel();
    });

    document.body.appendChild(indicator);
  }

  function showAuditPanel() {
    let panel = document.getElementById('gal-audit-panel');
    if (panel) {
      panel.remove();
      return;
    }

    panel = document.createElement('div');
    panel.id = 'gal-audit-panel';
    panel.style.cssText = 'position:fixed;top:40px;left:8px;z-index:99998;width:400px;max-height:60vh;overflow-y:auto;background:rgba(10,10,15,0.95);border:1px solid rgba(212,175,55,0.3);border-radius:12px;padding:16px;font-family:monospace;font-size:12px;color:#e0e0e0;backdrop-filter:blur(12px);';

    let html = '<div style="color:#d4af37;font-weight:bold;margin-bottom:8px;font-size:14px">GAL Architecture Info</div>';
    html += '<div style="margin-bottom:12px;padding:8px;background:rgba(105,240,174,0.08);border-radius:6px">';
    html += '<div><strong>Connection:</strong> ' + (GAL_CONFIG.connected ? '<span style="color:#69f0ae">Server-Side</span>' : '<span style="color:#ff5252">Offline</span>') + '</div>';
    html += '<div><strong>Player ID:</strong> ' + (GAL_CONFIG.playerId || 'N/A') + '</div>';
    html += '<div><strong>Session ID:</strong> ' + (GAL_CONFIG.sessionId || 'N/A') + '</div>';
    html += '<div><strong>Game:</strong> ' + (GAL_CONFIG.gameSlug || 'N/A') + '</div>';
    html += '<div><strong>Server RTP:</strong> ' + (GAL_CONFIG.serverRTP ? (GAL_CONFIG.serverRTP * 100).toFixed(2) + '%' : 'N/A') + '</div>';
    html += '<div><strong>RNG Buffer:</strong> ' + GAL_CONFIG.rngBuffer.length + ' numbers queued</div>';
    html += '<div><strong>Rounds Played:</strong> ' + GAL_CONFIG.roundsPlayed + '</div>';
    html += '<div><strong>Total Bet:</strong> $' + GAL_CONFIG.totalBet.toFixed(2) + '</div>';
    html += '<div><strong>Total Won:</strong> $' + GAL_CONFIG.totalWon.toFixed(2) + '</div>';
    if (GAL_CONFIG.totalBet > 0) {
      html += '<div><strong>Actual RTP:</strong> ' + ((GAL_CONFIG.totalWon / GAL_CONFIG.totalBet) * 100).toFixed(2) + '%</div>';
    }
    html += '</div>';

    html += '<div style="color:#d4af37;font-weight:bold;margin-bottom:4px">Architecture Flow</div>';
    html += '<div style="padding:8px;background:rgba(212,175,55,0.06);border-radius:6px;margin-bottom:8px;font-size:11px;line-height:1.6">';
    html += 'Browser &rarr; GAL API &rarr; Wallet (Event-Sourced) &rarr; RNG (CSPRNG) &rarr; PostgreSQL<br>';
    html += 'Balance: Server-side (not localStorage)<br>';
    html += 'RNG: Python secrets module (not Math.random)<br>';
    html += 'RTP: Database config (not localStorage)';
    html += '</div>';

    if (GAL_CONFIG.auditLog.length > 0) {
      html += '<div style="color:#d4af37;font-weight:bold;margin-bottom:4px">RNG Audit Trail (last 10)</div>';
      html += '<div style="font-size:10px">';
      GAL_CONFIG.auditLog.slice(-10).reverse().forEach(a => {
        html += '<div style="padding:4px;border-bottom:1px solid rgba(255,255,255,0.05)">Hash: ' + (a.seed_hash || a).substring(0, 32) + '...</div>';
      });
      html += '</div>';
    }

    html += '<div style="margin-top:8px;text-align:center;color:var(--text-dim,#888);font-size:10px">Click status bar to toggle this panel</div>';
    panel.innerHTML = html;
    document.body.appendChild(panel);
  }

  // ========================================
  // Helpers
  // ========================================

  function authHeaders() {
    const h = {'Content-Type': 'application/json'};
    if (GAL_CONFIG.token) h['Authorization'] = 'Bearer ' + GAL_CONFIG.token;
    return h;
  }

  function saveSession() {
    sessionStorage.setItem('gal_session', JSON.stringify({
      token: GAL_CONFIG.token,
      playerId: GAL_CONFIG.playerId,
      sessionId: GAL_CONFIG.sessionId,
      balance: GAL_CONFIG.balance
    }));
  }

  // ========================================
  // Auto-init when DOM is ready
  // ========================================

  // Detect game slug from page
  function detectGameSlug() {
    // Try from script tag data attribute
    const scripts = document.querySelectorAll('script[data-game]');
    if (scripts.length) return scripts[0].dataset.game;

    // Try from URL
    const path = window.location.pathname;
    const match = path.match(/games\/([^/.]+)/);
    if (match) return match[1];

    // Try from page content (look for GAME_SLUG variable)
    return null;
  }

  // Expose global API for games that want to use it directly
  window.GAL = {
    config: GAL_CONFIG,
    init: initSession,
    placeBet: placeBet,
    getBalance: () => GAL_CONFIG.balance,
    getRandom: getServerRandom,
    fetchBalance: fetchBalance,
    syncBalance: async function() {
      await fetchBalance();
      return GAL_CONFIG.balance;
    },
    isConnected: () => GAL_CONFIG.connected
  };

  // Auto-initialize when DOM loads
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      const slug = detectGameSlug();
      if (slug) initSession(slug);
    });
  } else {
    const slug = detectGameSlug();
    if (slug) initSession(slug);
  }

})();
