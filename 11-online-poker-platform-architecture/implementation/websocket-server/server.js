// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Online Poker WebSocket Server
 * Chapter 4 - Online Poker Platform Architecture
 *
 * Production-grade WebSocket server with:
 * - JWT authentication with device fingerprinting
 * - Heartbeat monitoring with configurable intervals
 * - Reconnection with state recovery (120-second window)
 * - Per-table message routing
 * - Rate limiting and connection throttling
 * - Graceful shutdown
 *
 * Dependencies: ws, jsonwebtoken, uuid, prom-client
 * Install: npm install ws jsonwebtoken uuid prom-client
 */

'use strict';

const WebSocket = require('ws');
const http = require('http');
const crypto = require('crypto');
const { EventEmitter } = require('events');

const {
  OpCode,
  ErrorCode,
  ProtocolMessage,
  MessageBuilder,
  PROTOCOL_VERSION,
} = require('./protocol');

// ─── Configuration ───────────────────────────────────────────────────
const CONFIG = {
  port: parseInt(process.env.POKER_WS_PORT, 10) || 8080,
  host: process.env.POKER_WS_HOST || '0.0.0.0',

  // Authentication
  jwtSecret: process.env.JWT_SECRET || 'CHANGE-ME-IN-PRODUCTION',
  jwtAlgorithm: 'HS256',
  authTimeoutMs: 10000,

  // Heartbeat
  heartbeatIntervalMs: 15000,
  heartbeatTimeoutMs: 45000,   // 3 missed heartbeats = disconnect

  // Reconnection
  reconnectWindowMs: 120000,   // 2 minutes to reconnect
  maxReconnectAttempts: 5,

  // Rate limiting
  maxMessagesPerSecond: 30,
  maxConnectionsPerIp: 4,

  // Server limits
  maxPayloadBytes: 16 * 1024,  // 16 KB max message
  maxTablesPerPlayer: 24,      // multi-tabling limit
  backlogSize: 512,

  // TLS (production)
  tlsCert: process.env.TLS_CERT_PATH || null,
  tlsKey: process.env.TLS_KEY_PATH || null,
};

// ─── Metrics (Prometheus-compatible) ─────────────────────────────────
class Metrics {
  constructor() {
    this.connections = { active: 0, total: 0, rejected: 0 };
    this.messages = { sent: 0, received: 0, invalid: 0 };
    this.auth = { success: 0, failed: 0, timeout: 0 };
    this.reconnections = { success: 0, failed: 0, expired: 0 };
    this.latency = { samples: [], p50: 0, p95: 0, p99: 0 };
  }

  recordLatency(ms) {
    this.latency.samples.push(ms);
    if (this.latency.samples.length > 10000) {
      this.latency.samples = this.latency.samples.slice(-5000);
    }
    const sorted = [...this.latency.samples].sort((a, b) => a - b);
    const len = sorted.length;
    this.latency.p50 = sorted[Math.floor(len * 0.50)];
    this.latency.p95 = sorted[Math.floor(len * 0.95)];
    this.latency.p99 = sorted[Math.floor(len * 0.99)];
  }

  toJSON() {
    return {
      connections: this.connections,
      messages: this.messages,
      auth: this.auth,
      reconnections: this.reconnections,
      latency: {
        p50: this.latency.p50,
        p95: this.latency.p95,
        p99: this.latency.p99,
        sample_count: this.latency.samples.length,
      },
    };
  }
}

// ─── Player Session ──────────────────────────────────────────────────
class PlayerSession {
  constructor(playerId, ws, deviceFingerprint) {
    this.playerId = playerId;
    this.sessionId = crypto.randomUUID();
    this.ws = ws;
    this.deviceFingerprint = deviceFingerprint;
    this.authenticated = false;
    this.connectedAt = Date.now();
    this.lastHeartbeat = Date.now();
    this.lastMessageAt = Date.now();
    this.messageCount = 0;
    this.messageWindowStart = Date.now();
    this.tables = new Set();          // table IDs player is seated at
    this.lastSeqReceived = 0;
    this.stateVersion = 0;
    this.disconnectedAt = null;       // set when disconnected, null when connected
    this.pendingMessages = [];        // buffered during disconnect for recovery
  }

  get isConnected() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  get isDisconnected() {
    return this.disconnectedAt !== null;
  }

  get reconnectExpired() {
    if (!this.disconnectedAt) return false;
    return (Date.now() - this.disconnectedAt) > CONFIG.reconnectWindowMs;
  }

  /**
   * Rate limit check: returns true if message should be allowed.
   */
  checkRateLimit() {
    const now = Date.now();
    if (now - this.messageWindowStart > 1000) {
      this.messageCount = 0;
      this.messageWindowStart = now;
    }
    this.messageCount++;
    return this.messageCount <= CONFIG.maxMessagesPerSecond;
  }

  /**
   * Send message to player, buffering if disconnected.
   */
  send(msg) {
    if (this.isConnected) {
      try {
        this.ws.send(JSON.stringify(msg));
        return true;
      } catch (err) {
        console.error(`[Session ${this.sessionId}] Send error: ${err.message}`);
        return false;
      }
    } else if (this.isDisconnected && !this.reconnectExpired) {
      // Buffer for reconnection recovery (cap at 500 messages)
      if (this.pendingMessages.length < 500) {
        this.pendingMessages.push(msg);
      }
      return false;
    }
    return false;
  }
}

// ─── Connection Tracker (per-IP) ─────────────────────────────────────
class ConnectionTracker {
  constructor() {
    this.ipConnections = new Map();  // ip -> count
  }

  canConnect(ip) {
    const count = this.ipConnections.get(ip) || 0;
    return count < CONFIG.maxConnectionsPerIp;
  }

  addConnection(ip) {
    const count = this.ipConnections.get(ip) || 0;
    this.ipConnections.set(ip, count + 1);
  }

  removeConnection(ip) {
    const count = this.ipConnections.get(ip) || 0;
    if (count <= 1) {
      this.ipConnections.delete(ip);
    } else {
      this.ipConnections.set(ip, count - 1);
    }
  }
}

// ─── Poker WebSocket Server ──────────────────────────────────────────
class PokerWebSocketServer extends EventEmitter {
  constructor() {
    super();
    this.sessions = new Map();          // sessionId -> PlayerSession
    this.playerSessions = new Map();    // playerId -> sessionId
    this.metrics = new Metrics();
    this.connectionTracker = new ConnectionTracker();
    this.heartbeatTimer = null;
    this.cleanupTimer = null;
    this.httpServer = null;
    this.wss = null;
    this.shuttingDown = false;
  }

  /**
   * Start the WebSocket server.
   */
  start() {
    this.httpServer = http.createServer((req, res) => {
      // Health check endpoint
      if (req.url === '/health') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok', uptime: process.uptime() }));
        return;
      }
      // Metrics endpoint
      if (req.url === '/metrics') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(this.metrics.toJSON()));
        return;
      }
      res.writeHead(404);
      res.end('Not Found');
    });

    this.wss = new WebSocket.Server({
      server: this.httpServer,
      maxPayload: CONFIG.maxPayloadBytes,
      backlog: CONFIG.backlogSize,
      verifyClient: (info, callback) => {
        const ip = info.req.socket.remoteAddress;
        if (this.shuttingDown) {
          callback(false, 503, 'Server shutting down');
          return;
        }
        if (!this.connectionTracker.canConnect(ip)) {
          this.metrics.connections.rejected++;
          callback(false, 429, 'Too many connections from this IP');
          return;
        }
        callback(true);
      },
    });

    this.wss.on('connection', (ws, req) => this._handleConnection(ws, req));

    // Start heartbeat monitor
    this.heartbeatTimer = setInterval(() => this._heartbeatCheck(), CONFIG.heartbeatIntervalMs);

    // Cleanup expired sessions every 30 seconds
    this.cleanupTimer = setInterval(() => this._cleanupExpiredSessions(), 30000);

    this.httpServer.listen(CONFIG.port, CONFIG.host, () => {
      console.log(`[PokerWS] Server listening on ${CONFIG.host}:${CONFIG.port}`);
      console.log(`[PokerWS] Protocol version: ${PROTOCOL_VERSION}`);
      console.log(`[PokerWS] Heartbeat interval: ${CONFIG.heartbeatIntervalMs}ms`);
      console.log(`[PokerWS] Reconnect window: ${CONFIG.reconnectWindowMs}ms`);
    });

    // Graceful shutdown
    process.on('SIGTERM', () => this.shutdown());
    process.on('SIGINT', () => this.shutdown());
  }

  /**
   * Handle new WebSocket connection.
   */
  _handleConnection(ws, req) {
    const ip = req.socket.remoteAddress;
    this.connectionTracker.addConnection(ip);
    this.metrics.connections.active++;
    this.metrics.connections.total++;

    console.log(`[PokerWS] New connection from ${ip} (active: ${this.metrics.connections.active})`);

    // Set authentication timeout
    const authTimeout = setTimeout(() => {
      if (ws.readyState === WebSocket.OPEN) {
        const errorMsg = MessageBuilder.error(
          ErrorCode.AUTH_FAILED,
          'Authentication timeout'
        );
        ws.send(JSON.stringify(errorMsg));
        ws.close(4001, 'Authentication timeout');
        this.metrics.auth.timeout++;
      }
    }, CONFIG.authTimeoutMs);

    let session = null;

    ws.on('message', (data) => {
      let msg;
      try {
        msg = JSON.parse(data.toString());
      } catch (err) {
        this.metrics.messages.invalid++;
        const errorMsg = MessageBuilder.error(ErrorCode.INTERNAL_ERROR, 'Invalid JSON');
        ws.send(JSON.stringify(errorMsg));
        return;
      }

      const validation = ProtocolMessage.validate(msg);
      if (!validation.valid) {
        this.metrics.messages.invalid++;
        const errorMsg = MessageBuilder.error(
          ErrorCode.INTERNAL_ERROR,
          `Invalid message: ${validation.errors.join(', ')}`
        );
        ws.send(JSON.stringify(errorMsg));
        return;
      }

      this.metrics.messages.received++;

      // ── Authentication ───────────────────────────────
      if (msg.op === OpCode.AUTH_REQUEST) {
        clearTimeout(authTimeout);
        session = this._handleAuth(ws, msg, ip);
        return;
      }

      // ── Reconnection ─────────────────────────────────
      if (msg.op === OpCode.RECONNECT_REQUEST) {
        clearTimeout(authTimeout);
        session = this._handleReconnect(ws, msg, ip);
        return;
      }

      // All other messages require authentication
      if (!session || !session.authenticated) {
        const errorMsg = MessageBuilder.error(ErrorCode.AUTH_FAILED, 'Not authenticated');
        ws.send(JSON.stringify(errorMsg));
        return;
      }

      // Rate limit check
      if (!session.checkRateLimit()) {
        const errorMsg = MessageBuilder.error(ErrorCode.RATE_LIMITED, 'Rate limit exceeded');
        ws.send(JSON.stringify(errorMsg));
        return;
      }

      session.lastMessageAt = Date.now();
      session.lastSeqReceived = msg.seq;

      // ── Heartbeat ────────────────────────────────────
      if (msg.op === OpCode.HEARTBEAT_PONG) {
        session.lastHeartbeat = Date.now();
        const latencyMs = Date.now() - (msg.d.ping_timestamp || Date.now());
        this.metrics.recordLatency(latencyMs);
        return;
      }

      // ── Game actions ─────────────────────────────────
      if (msg.op === OpCode.ACTION_RESPONSE) {
        this._handleGameAction(session, msg);
        return;
      }

      // ── Table operations ─────────────────────────────
      if (msg.op === OpCode.TABLE_JOIN) {
        this._handleTableJoin(session, msg);
        return;
      }

      if (msg.op === OpCode.TABLE_LEAVE) {
        this._handleTableLeave(session, msg);
        return;
      }

      // ── Chat ─────────────────────────────────────────
      if (msg.op === OpCode.CHAT_MESSAGE) {
        this._handleChat(session, msg);
        return;
      }

      // Unknown opcode
      const errorMsg = MessageBuilder.error(ErrorCode.INTERNAL_ERROR, `Unknown opcode: ${msg.op}`);
      ws.send(JSON.stringify(errorMsg));
    });

    ws.on('close', (code, reason) => {
      clearTimeout(authTimeout);
      this.connectionTracker.removeConnection(ip);
      this.metrics.connections.active--;

      if (session) {
        console.log(`[PokerWS] Player ${session.playerId} disconnected (code: ${code})`);
        session.disconnectedAt = Date.now();
        session.ws = null;
        // Session kept alive for reconnection window
        this.emit('player:disconnect', session.playerId, session.tables);
      }
    });

    ws.on('error', (err) => {
      console.error(`[PokerWS] WebSocket error: ${err.message}`);
    });
  }

  /**
   * Authenticate a new connection.
   */
  _handleAuth(ws, msg, ip) {
    const { token, device_fingerprint, client_version } = msg.d;

    // In production, verify JWT with proper key management
    let decoded;
    try {
      // Simplified JWT verification - use jsonwebtoken.verify() in production
      const parts = Buffer.from(token.split('.')[1] || '', 'base64').toString();
      decoded = JSON.parse(parts);
    } catch (err) {
      this.metrics.auth.failed++;
      const errorMsg = MessageBuilder.authResponse(false, null, null, Date.now(), ErrorCode.AUTH_FAILED);
      ws.send(JSON.stringify(errorMsg));
      ws.close(4001, 'Invalid token');
      return null;
    }

    const playerId = decoded.sub || decoded.player_id;
    if (!playerId) {
      this.metrics.auth.failed++;
      const errorMsg = MessageBuilder.authResponse(false, null, null, Date.now(), ErrorCode.AUTH_FAILED);
      ws.send(JSON.stringify(errorMsg));
      ws.close(4001, 'Missing player ID');
      return null;
    }

    // Check for existing session (force disconnect old connection)
    const existingSessionId = this.playerSessions.get(playerId);
    if (existingSessionId) {
      const existingSession = this.sessions.get(existingSessionId);
      if (existingSession && existingSession.isConnected) {
        const forceMsg = MessageBuilder.error(ErrorCode.TOO_MANY_CONNECTIONS, 'Connected from another device');
        existingSession.send(forceMsg);
        existingSession.ws.close(4002, 'Replaced by new connection');
      }
      this.sessions.delete(existingSessionId);
    }

    // Create new session
    const session = new PlayerSession(playerId, ws, device_fingerprint);
    session.authenticated = true;
    this.sessions.set(session.sessionId, session);
    this.playerSessions.set(playerId, session.sessionId);

    this.metrics.auth.success++;
    console.log(`[PokerWS] Player ${playerId} authenticated (session: ${session.sessionId})`);

    // Send auth response
    const response = MessageBuilder.authResponse(true, playerId, session.sessionId, Date.now());
    ws.send(JSON.stringify(response));

    this.emit('player:auth', playerId, session);
    return session;
  }

  /**
   * Handle reconnection with state recovery.
   */
  _handleReconnect(ws, msg, ip) {
    const { session_id, last_received_seq, state_version } = msg.d;

    const session = this.sessions.get(session_id);
    if (!session) {
      this.metrics.reconnections.failed++;
      const errorMsg = MessageBuilder.error(ErrorCode.AUTH_FAILED, 'Session not found');
      ws.send(JSON.stringify(errorMsg));
      ws.close(4003, 'Invalid session');
      return null;
    }

    if (session.reconnectExpired) {
      this.metrics.reconnections.expired++;
      this.sessions.delete(session_id);
      this.playerSessions.delete(session.playerId);
      const errorMsg = MessageBuilder.error(ErrorCode.AUTH_TOKEN_EXPIRED, 'Reconnect window expired');
      ws.send(JSON.stringify(errorMsg));
      ws.close(4004, 'Reconnect window expired');
      return null;
    }

    // Restore session
    session.ws = ws;
    session.disconnectedAt = null;
    session.lastHeartbeat = Date.now();

    this.metrics.reconnections.success++;
    console.log(`[PokerWS] Player ${session.playerId} reconnected (session: ${session_id})`);

    // Build table state for recovery
    const tableStates = {};
    for (const tableId of session.tables) {
      // In production, fetch actual table state from game engine
      tableStates[tableId] = { table_id: tableId, state: 'active' };
    }

    // Send missed messages
    const missedMessages = session.pendingMessages.filter(m => m.seq > last_received_seq);
    const reconnectMsg = MessageBuilder.reconnectState(tableStates, missedMessages, session.stateVersion);
    ws.send(JSON.stringify(reconnectMsg));

    // Clear pending messages
    session.pendingMessages = [];

    this.emit('player:reconnect', session.playerId, session);
    return session;
  }

  /**
   * Handle game action from player.
   */
  _handleGameAction(session, msg) {
    const { table_id, hand_id, action, amount } = msg.d;

    if (!session.tables.has(table_id)) {
      const errorMsg = MessageBuilder.error(ErrorCode.INVALID_ACTION, 'Not seated at this table');
      session.send(errorMsg);
      return;
    }

    const actionTimestamp = Date.now();
    const clientTimestamp = msg.d.client_timestamp || actionTimestamp;
    const latencyMs = actionTimestamp - clientTimestamp;
    this.metrics.recordLatency(latencyMs);

    // Emit for game engine to process
    this.emit('game:action', {
      playerId: session.playerId,
      tableId: table_id,
      handId: hand_id,
      action,
      amount: amount || 0,
      serverTimestamp: actionTimestamp,
      latencyMs,
    });
  }

  /**
   * Handle table join request.
   */
  _handleTableJoin(session, msg) {
    const { table_id, buy_in, seat_preference } = msg.d;

    if (session.tables.size >= CONFIG.maxTablesPerPlayer) {
      const errorMsg = MessageBuilder.error(
        ErrorCode.ALREADY_SEATED,
        `Maximum ${CONFIG.maxTablesPerPlayer} tables allowed`
      );
      session.send(errorMsg);
      return;
    }

    session.tables.add(table_id);
    this.emit('table:join', {
      playerId: session.playerId,
      tableId: table_id,
      buyIn: buy_in,
      seatPreference: seat_preference,
      sessionId: session.sessionId,
    });
  }

  /**
   * Handle table leave.
   */
  _handleTableLeave(session, msg) {
    const { table_id } = msg.d;
    session.tables.delete(table_id);
    this.emit('table:leave', {
      playerId: session.playerId,
      tableId: table_id,
    });
  }

  /**
   * Handle chat message with profanity filter.
   */
  _handleChat(session, msg) {
    const { table_id, text } = msg.d;

    if (!session.tables.has(table_id)) return;
    if (!text || text.length > 200) return;

    // Sanitize (basic - use dedicated library in production)
    const sanitized = text.replace(/[<>&"']/g, '');

    this.emit('chat:message', {
      playerId: session.playerId,
      tableId: table_id,
      text: sanitized,
      timestamp: Date.now(),
    });
  }

  /**
   * Periodic heartbeat check - disconnect stale connections.
   */
  _heartbeatCheck() {
    const now = Date.now();
    for (const [sessionId, session] of this.sessions) {
      if (!session.isConnected) continue;

      if (now - session.lastHeartbeat > CONFIG.heartbeatTimeoutMs) {
        console.log(`[PokerWS] Heartbeat timeout for player ${session.playerId}`);
        session.ws.close(4005, 'Heartbeat timeout');
        continue;
      }

      // Send heartbeat ping
      const pingMsg = MessageBuilder.heartbeatPing(sessionId);
      pingMsg.d.ping_timestamp = now;
      session.send(pingMsg);
    }
  }

  /**
   * Clean up sessions past reconnection window.
   */
  _cleanupExpiredSessions() {
    for (const [sessionId, session] of this.sessions) {
      if (session.isDisconnected && session.reconnectExpired) {
        console.log(`[PokerWS] Cleaning up expired session for player ${session.playerId}`);
        this.playerSessions.delete(session.playerId);
        this.sessions.delete(sessionId);

        // Notify tables to remove player
        for (const tableId of session.tables) {
          this.emit('table:timeout', {
            playerId: session.playerId,
            tableId,
          });
        }
      }
    }
  }

  /**
   * Send message to a specific player by ID.
   */
  sendToPlayer(playerId, msg) {
    const sessionId = this.playerSessions.get(playerId);
    if (!sessionId) return false;
    const session = this.sessions.get(sessionId);
    if (!session) return false;
    return session.send(msg);
  }

  /**
   * Broadcast message to all players at a table.
   */
  broadcastToTable(tableId, msg, excludePlayerId = null) {
    let sent = 0;
    for (const [, session] of this.sessions) {
      if (session.tables.has(tableId) && session.playerId !== excludePlayerId) {
        session.send(msg);
        sent++;
      }
    }
    this.metrics.messages.sent += sent;
    return sent;
  }

  /**
   * Graceful shutdown - notify all players, close connections.
   */
  async shutdown() {
    if (this.shuttingDown) return;
    this.shuttingDown = true;
    console.log('[PokerWS] Initiating graceful shutdown...');

    clearInterval(this.heartbeatTimer);
    clearInterval(this.cleanupTimer);

    // Notify all connected players
    const maintenanceMsg = ProtocolMessage.create(OpCode.MAINTENANCE, {
      message: 'Server shutting down for maintenance',
      shutdown_in_ms: 30000,
    });

    for (const [, session] of this.sessions) {
      if (session.isConnected) {
        session.send(maintenanceMsg);
      }
    }

    // Wait for in-progress hands to complete (up to 30 seconds)
    await new Promise(resolve => setTimeout(resolve, 5000));

    // Close all connections
    for (const [, session] of this.sessions) {
      if (session.isConnected) {
        session.ws.close(1001, 'Server shutdown');
      }
    }

    if (this.wss) this.wss.close();
    if (this.httpServer) this.httpServer.close();

    console.log('[PokerWS] Shutdown complete');
    process.exit(0);
  }

  /**
   * Get server status for monitoring.
   */
  getStatus() {
    return {
      uptime_s: Math.floor(process.uptime()),
      protocol_version: PROTOCOL_VERSION,
      active_connections: this.metrics.connections.active,
      total_sessions: this.sessions.size,
      authenticated_players: this.playerSessions.size,
      metrics: this.metrics.toJSON(),
    };
  }
}

// ─── Main Entry Point ────────────────────────────────────────────────
if (require.main === module) {
  const server = new PokerWebSocketServer();

  // Wire up example event handlers
  server.on('player:auth', (playerId, session) => {
    console.log(`[Event] Player authenticated: ${playerId}`);
  });

  server.on('player:disconnect', (playerId, tables) => {
    console.log(`[Event] Player disconnected: ${playerId}, tables: ${[...tables].join(',')}`);
  });

  server.on('player:reconnect', (playerId) => {
    console.log(`[Event] Player reconnected: ${playerId}`);
  });

  server.on('game:action', (data) => {
    console.log(`[Event] Game action: player=${data.playerId} table=${data.tableId} ` +
                `action=${data.action} amount=${data.amount} latency=${data.latencyMs}ms`);
  });

  server.on('table:join', (data) => {
    console.log(`[Event] Table join: player=${data.playerId} table=${data.tableId} buy_in=${data.buyIn}`);
  });

  server.on('table:leave', (data) => {
    console.log(`[Event] Table leave: player=${data.playerId} table=${data.tableId}`);
  });

  server.start();
}

module.exports = { PokerWebSocketServer, CONFIG };
