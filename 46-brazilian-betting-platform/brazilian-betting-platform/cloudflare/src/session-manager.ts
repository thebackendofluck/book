// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * BettingSession Durable Object
 *
 * Manages the lifecycle of a single player session within a single operator.
 * One Durable Object instance per `{playerId}:{operatorId}` pair.
 *
 * State machine:
 *   active ──(30 min elapsed)──► reverifying ──(geo confirmed)──► active
 *                                             └──(timeout / fail)──► expired
 *
 * The 30-minute geolocation re-verification requirement is mandated by
 * SIGAP (Portaria SPA/MF 827/2023, Art. 38) to prevent account sharing
 * across different physical locations during a session.
 *
 * WebSocket support:
 *   Clients may upgrade the connection to WebSocket on /session/ws.
 *   The Durable Object broadcasts session state changes (e.g., reverify
 *   prompts) to all connected clients in real time.
 */

import type { PlayerSession, GeoVerification, BrazilRequest } from './types.js';

export default {};

// ── Session constants ─────────────────────────────────────────────────────────

const GEO_REVERIFY_INTERVAL_MS = 30 * 60 * 1000;  // 30 minutes
const GEO_REVERIFY_WINDOW_MS   =  5 * 60 * 1000;  // 5 minutes to complete re-verify

// ── Durable Object class ──────────────────────────────────────────────────────

export class BettingSession {
  private state:   DurableObjectState;
  private sockets: Set<WebSocket> = new Set();

  constructor(state: DurableObjectState) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    switch (url.pathname) {
      case '/session/start':
        return this.handleStart(request);

      case '/session/state':
        return this.handleGetState();

      case '/session/end':
        return this.handleEnd();

      case '/session/verify-geo':
        return this.handleGeoVerification(request);

      case '/session/ws':
        return this.handleWebSocket(request);

      case '/session/heartbeat':
        return this.handleHeartbeat();

      default:
        return new Response('Not found', { status: 404 });
    }
  }

  // ── Start session ──────────────────────────────────────────────────────────

  private async handleStart(request: Request): Promise<Response> {
    const body = await parseJSON<{
      playerId: string;
      cpf: string;
      country: string;
      operatorId: string;
      deviceFingerprint?: string;
    }>(request);

    if (!body || !body.playerId || !body.cpf || !body.country || !body.operatorId) {
      return jsonError('playerId, cpf, country e operatorId são obrigatórios.', 400);
    }

    const now: PlayerSession = {
      playerId:          body.playerId,
      cpf:               body.cpf,
      country:           body.country,
      state:             'active',
      createdAt:         Date.now(),
      lastActivity:      Date.now(),
      reverifyAt:        Date.now() + GEO_REVERIFY_INTERVAL_MS,
      operatorId:        body.operatorId,
      deviceFingerprint: body.deviceFingerprint,
    };

    await this.state.storage.put('session', now);

    // Schedule the first geo re-verify alarm
    await this.state.storage.setAlarm(Date.now() + GEO_REVERIFY_INTERVAL_MS);

    this.broadcast({ type: 'session_started', session: this.sanitize(now) });

    return jsonSuccess({ session: this.sanitize(now) }, 201);
  }

  // ── Get session state ──────────────────────────────────────────────────────

  private async handleGetState(): Promise<Response> {
    const session = await this.state.storage.get<PlayerSession>('session');

    if (!session) {
      return jsonError('Session not found.', 404);
    }

    // Proactively transition to reverifying if interval has elapsed
    if (session.state === 'active' && Date.now() >= session.reverifyAt) {
      session.state = 'reverifying';
      await this.state.storage.put('session', session);
      this.broadcast({ type: 'reverify_required', session: this.sanitize(session) });
    }

    return jsonSuccess({ session: this.sanitize(session) });
  }

  // ── End session ────────────────────────────────────────────────────────────

  private async handleEnd(): Promise<Response> {
    const session = await this.state.storage.get<PlayerSession>('session');

    if (session) {
      session.state = 'expired';
      await this.state.storage.put('session', session);
      this.broadcast({ type: 'session_ended' });
    }

    await this.state.storage.deleteAlarm();
    this.closeAllSockets('session_ended');

    return jsonSuccess({ ended: true });
  }

  // ── Geo re-verification ────────────────────────────────────────────────────

  private async handleGeoVerification(request: Request): Promise<Response> {
    const session = await this.state.storage.get<PlayerSession>('session');

    if (!session) {
      return jsonError('Session not found.', 404);
    }

    if (session.state === 'expired') {
      return jsonError('Session expired.', 410);
    }

    const body = await parseJSON<{ country: string; latitude?: number; longitude?: number }>(request);
    if (!body?.country) {
      return jsonError('country é obrigatório.', 400);
    }

    // Cloudflare's edge-derived country is the authoritative signal: it is
    // set from the connecting IP at the network layer and cannot be
    // spoofed the way a client-supplied JSON body field can. The client
    // claim is cross-checked against it, never trusted on its own.
    const cfRequest = request as BrazilRequest;
    const cfCountry = cfRequest.cf?.country ?? request.headers.get('CF-IPCountry') ?? undefined;

    // Brazil-only enforcement: require a trustworthy signal that the
    // player is still in BR, and keep the session suspended (do not
    // extend it) on any mismatch or missing signal.
    if (!cfCountry || cfCountry !== 'BR' || body.country !== cfCountry) {
      session.state = 'expired';
      await this.state.storage.put('session', session);
      await this.state.storage.deleteAlarm();
      this.closeAllSockets('geo_failed');
      return jsonError('Serviço disponível apenas no Brasil.', 403);
    }

    // Reset to active with a fresh reverify timer
    session.state        = 'active';
    session.country      = cfCountry;
    session.lastActivity = Date.now();
    session.reverifyAt   = Date.now() + GEO_REVERIFY_INTERVAL_MS;

    await this.state.storage.put('session', session);
    await this.state.storage.setAlarm(Date.now() + GEO_REVERIFY_INTERVAL_MS);

    const geo: GeoVerification = {
      country:    body.country,
      latitude:   body.latitude,
      longitude:  body.longitude,
      verifiedAt: new Date().toISOString(),
    };

    // Append to geo history (last 10 verifications)
    const history = (await this.state.storage.get<GeoVerification[]>('geo_history')) ?? [];
    history.push(geo);
    if (history.length > 10) history.shift();
    await this.state.storage.put('geo_history', history);

    this.broadcast({ type: 'geo_verified', session: this.sanitize(session) });

    return jsonSuccess({ verified: true, nextVerifyAt: new Date(session.reverifyAt).toISOString() });
  }

  // ── WebSocket handler ──────────────────────────────────────────────────────

  private handleWebSocket(request: Request): Response {
    const upgradeHeader = request.headers.get('Upgrade');
    if (upgradeHeader !== 'websocket') {
      return new Response('Expected WebSocket upgrade.', { status: 426 });
    }

    const { 0: client, 1: server } = new WebSocketPair();

    server.accept();
    this.sockets.add(server);

    server.addEventListener('message', async (event) => {
      // Handle heartbeat pings from the client
      if (event.data === 'ping') {
        server.send('pong');
        await this.handleHeartbeat();
      }
    });

    server.addEventListener('close', () => {
      this.sockets.delete(server);
    });

    server.addEventListener('error', () => {
      this.sockets.delete(server);
    });

    return new Response(null, { status: 101, webSocket: client });
  }

  // ── Heartbeat (update lastActivity) ───────────────────────────────────────

  private async handleHeartbeat(): Promise<Response> {
    const session = await this.state.storage.get<PlayerSession>('session');
    if (!session) return jsonError('Session not found.', 404);

    if (session.state !== 'active') {
      return jsonError(`Session is ${session.state}.`, 409);
    }

    session.lastActivity = Date.now();
    await this.state.storage.put('session', session);

    return jsonSuccess({ ok: true });
  }

  // ── Durable Object alarm ───────────────────────────────────────────────────

  /**
   * Called by the Cloudflare runtime when the alarm fires (30 minutes after
   * session start or last successful geo verification).
   */
  async alarm(): Promise<void> {
    const session = await this.state.storage.get<PlayerSession>('session');
    if (!session || session.state === 'expired') return;

    if (session.state === 'active') {
      // Transition to reverifying and give the player a 5-minute window
      session.state = 'reverifying';
      await this.state.storage.put('session', session);
      await this.state.storage.setAlarm(Date.now() + GEO_REVERIFY_WINDOW_MS);
      this.broadcast({ type: 'reverify_required', expiresIn: GEO_REVERIFY_WINDOW_MS / 1000 });
      return;
    }

    if (session.state === 'reverifying') {
      // Grace period elapsed without geo re-verification — expire the session
      session.state = 'expired';
      await this.state.storage.put('session', session);
      this.closeAllSockets('reverify_timeout');
      return;
    }
  }

  // ── Internal helpers ───────────────────────────────────────────────────────

  /** Broadcast a message to all connected WebSocket clients. */
  private broadcast(message: Record<string, unknown>): void {
    const json = JSON.stringify(message);
    for (const ws of this.sockets) {
      try {
        ws.send(json);
      } catch {
        this.sockets.delete(ws);
      }
    }
  }

  private closeAllSockets(reason: string): void {
    for (const ws of this.sockets) {
      try { ws.close(1000, reason); } catch { /* already closed */ }
    }
    this.sockets.clear();
  }

  /** Strip sensitive fields before sending session state to clients. */
  private sanitize(session: PlayerSession): Omit<PlayerSession, 'cpf'> & { cpf: string } {
    return {
      ...session,
      cpf: `***.***.${session.cpf.slice(6, 9)}-${session.cpf.slice(9)}`,
    };
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function jsonSuccess(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ success: true, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ success: false, error: message }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function parseJSON<T>(req: Request): Promise<T | null> {
  try { return (await req.json()) as T; } catch { return null; }
}
