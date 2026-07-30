// Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * streamRecovery.ts — Stream Recovery and Failover Coordinator
 *
 * Monitors active streaming sessions and coordinates automatic recovery
 * when a stream fails: RTMP disconnect, transcoder crash, or SFU worker death.
 *
 * Recovery sequence:
 *   1. Detect stream failure (heartbeat timeout or error event)
 *   2. Notify connected players (stream interruption overlay)
 *   3. Attempt restart on same ingest node
 *   4. If restart fails: failover to secondary RTMP ingest
 *   5. If secondary fails: activate HLS fallback from CDN
 *   6. Notify operations team if all recovery paths exhausted
 *
 * RTO target: < 10 seconds for automatic recovery
 *
 * Chapter 13 — Live Casino Streaming Infrastructure
 */

import EventEmitter from 'events';

interface StreamSession {
  tableId: string;
  primaryRtmpUrl: string;
  secondaryRtmpUrl: string;
  hlsFallbackUrl: string;
  status: 'active' | 'recovering' | 'degraded' | 'failed';
  lastHeartbeat: number;
  recoveryAttempts: number;
  maxRecoveryAttempts: number;
  currentSource: 'primary_rtmp' | 'secondary_rtmp' | 'hls_fallback';
  playerCount: number;
}

interface RecoveryResult {
  tableId: string;
  success: boolean;
  recoveredSource: StreamSession['currentSource'] | null;
  recoveryTimeMs: number;
  attemptNumber: number;
}

interface RecoveryOptions {
  heartbeatTimeoutMs?: number;
  recoveryDelayMs?: number;
  maxAttempts?: number;
  notifyPlayers?: (tableId: string, message: string) => Promise<void>;
  notifyOps?: (tableId: string, details: object) => Promise<void>;
  logger?: Console;
}

// ---------------------------------------------------------------------------
// Stream recovery coordinator
// ---------------------------------------------------------------------------

export class StreamRecoveryCoordinator extends EventEmitter {
  private readonly sessions = new Map<string, StreamSession>();
  private readonly heartbeatTimer: ReturnType<typeof setInterval>;
  private readonly heartbeatTimeoutMs: number;
  private readonly recoveryDelayMs: number;
  private readonly notifyPlayers: (tableId: string, message: string) => Promise<void>;
  private readonly notifyOps: (tableId: string, details: object) => Promise<void>;
  private readonly log: Console;

  constructor(options: RecoveryOptions = {}) {
    super();
    this.heartbeatTimeoutMs = options.heartbeatTimeoutMs ?? 15_000;
    this.recoveryDelayMs = options.recoveryDelayMs ?? 2_000;
    this.notifyPlayers = options.notifyPlayers ?? (async (tableId, msg) => {
      console.log(`[NOTIFY PLAYERS] Table ${tableId}: ${msg}`);
    });
    this.notifyOps = options.notifyOps ?? (async (tableId, details) => {
      console.error(`[OPS ALERT] Table ${tableId}:`, details);
    });
    this.log = options.logger ?? console;

    // Check heartbeats every 5 seconds
    this.heartbeatTimer = setInterval(() => this._checkHeartbeats(), 5_000);
  }

  // ---------------------------------------------------------------------------
  // Session registration
  // ---------------------------------------------------------------------------

  registerSession(params: Omit<StreamSession, 'status' | 'lastHeartbeat' | 'recoveryAttempts' | 'currentSource'>): void {
    const session: StreamSession = {
      ...params,
      status: 'active',
      lastHeartbeat: Date.now(),
      recoveryAttempts: 0,
      currentSource: 'primary_rtmp',
    };
    this.sessions.set(params.tableId, session);
    this.log.info(`Stream session registered for table ${params.tableId}`);
  }

  unregisterSession(tableId: string): void {
    this.sessions.delete(tableId);
    this.log.info(`Stream session unregistered for table ${tableId}`);
  }

  heartbeat(tableId: string): void {
    const session = this.sessions.get(tableId);
    if (session) {
      session.lastHeartbeat = Date.now();
      if (session.status === 'recovering') {
        session.status = 'active';
        session.recoveryAttempts = 0;
        this.log.info(`Table ${tableId} recovered — heartbeat resumed`);
        this.emit('stream:recovered', { tableId });
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Heartbeat monitoring
  // ---------------------------------------------------------------------------

  private _checkHeartbeats(): void {
    const now = Date.now();
    for (const [tableId, session] of this.sessions) {
      if (session.status === 'failed') continue;

      const elapsed = now - session.lastHeartbeat;
      if (elapsed > this.heartbeatTimeoutMs && session.status === 'active') {
        this.log.warn(
          `Table ${tableId}: heartbeat timeout (${elapsed}ms > ${this.heartbeatTimeoutMs}ms)`
        );
        this._initiateRecovery(session).catch((err) => {
          this.log.error(`Recovery failed for table ${tableId}:`, err);
        });
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Recovery sequence
  // ---------------------------------------------------------------------------

  async recoverStream(tableId: string): Promise<RecoveryResult> {
    const session = this.sessions.get(tableId);
    if (!session) {
      throw new Error(`No session found for table ${tableId}`);
    }
    return this._initiateRecovery(session);
  }

  private async _initiateRecovery(session: StreamSession): Promise<RecoveryResult> {
    const startTime = Date.now();
    session.status = 'recovering';
    session.recoveryAttempts++;

    this.log.warn(
      `Initiating recovery for table ${session.tableId} (attempt ${session.recoveryAttempts})`
    );

    this.emit('stream:failing', {
      tableId: session.tableId,
      attempt: session.recoveryAttempts,
    });

    // Notify players of interruption
    await this.notifyPlayers(
      session.tableId,
      'Stream interrupted. Attempting to reconnect...'
    ).catch(() => {});

    await this._delay(this.recoveryDelayMs);

    // --- Recovery path 1: restart primary RTMP ---
    if (session.currentSource === 'primary_rtmp') {
      this.log.info(`Table ${session.tableId}: attempting primary RTMP restart`);
      const restored = await this._testStreamSource(session.primaryRtmpUrl);
      if (restored) {
        return this._recoverySuccess(session, 'primary_rtmp', startTime);
      }
    }

    // --- Recovery path 2: failover to secondary RTMP ---
    if (session.currentSource !== 'secondary_rtmp') {
      this.log.info(`Table ${session.tableId}: failing over to secondary RTMP`);
      const secondary = await this._testStreamSource(session.secondaryRtmpUrl);
      if (secondary) {
        session.currentSource = 'secondary_rtmp';
        session.status = 'degraded'; // Degraded: on backup source
        this.emit('stream:failover', {
          tableId: session.tableId,
          from: 'primary_rtmp',
          to: 'secondary_rtmp',
        });
        return this._recoverySuccess(session, 'secondary_rtmp', startTime);
      }
    }

    // --- Recovery path 3: HLS fallback ---
    if (session.currentSource !== 'hls_fallback') {
      this.log.warn(`Table ${session.tableId}: activating HLS CDN fallback`);
      const hls = await this._testStreamSource(session.hlsFallbackUrl);
      if (hls) {
        session.currentSource = 'hls_fallback';
        session.status = 'degraded';
        this.emit('stream:hls-fallback', { tableId: session.tableId });
        await this.notifyPlayers(
          session.tableId,
          'Switched to lower-latency backup stream'
        ).catch(() => {});
        return this._recoverySuccess(session, 'hls_fallback', startTime);
      }
    }

    // --- All paths exhausted ---
    if (session.recoveryAttempts >= (session.maxRecoveryAttempts || 5)) {
      session.status = 'failed';
      this.log.error(`Table ${session.tableId}: all recovery paths exhausted`);
      this.emit('stream:failed', { tableId: session.tableId });
      await this.notifyOps(session.tableId, {
        message: 'Stream permanently failed — manual intervention required',
        recoveryAttempts: session.recoveryAttempts,
        currentSource: session.currentSource,
      }).catch(() => {});
    }

    return {
      tableId: session.tableId,
      success: false,
      recoveredSource: null,
      recoveryTimeMs: Date.now() - startTime,
      attemptNumber: session.recoveryAttempts,
    };
  }

  private _recoverySuccess(
    session: StreamSession,
    source: StreamSession['currentSource'],
    startTime: number
  ): RecoveryResult {
    session.status = source === 'primary_rtmp' ? 'active' : 'degraded';
    session.lastHeartbeat = Date.now();

    const result: RecoveryResult = {
      tableId: session.tableId,
      success: true,
      recoveredSource: source,
      recoveryTimeMs: Date.now() - startTime,
      attemptNumber: session.recoveryAttempts,
    };

    this.log.info(
      `Table ${session.tableId}: recovered via ${source} in ${result.recoveryTimeMs}ms`
    );
    this.emit('stream:recovered', result);
    return result;
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private async _testStreamSource(url: string): Promise<boolean> {
    try {
      const response = await fetch(url, {
        signal: AbortSignal.timeout(3_000),
        method: 'HEAD',
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  private _delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  getSessionStatus(tableId: string): Partial<StreamSession> | null {
    const session = this.sessions.get(tableId);
    if (!session) return null;
    const { tableId: tid, status, currentSource, recoveryAttempts, playerCount } = session;
    return { tableId: tid, status, currentSource, recoveryAttempts, playerCount };
  }

  destroy(): void {
    clearInterval(this.heartbeatTimer);
    this.sessions.clear();
  }
}

export type { StreamSession, RecoveryResult };
