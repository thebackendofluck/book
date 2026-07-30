// Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * webSocketManager.ts — Resilient WebSocket Connection Manager
 *
 * Manages a single WebSocket connection to the casino platform with:
 *   - Automatic reconnection with exponential backoff
 *   - JWT token refresh on expiry
 *   - Message queuing during disconnection
 *   - Heartbeat/ping mechanism to detect silent disconnections
 *   - Event emitter pattern for consumers
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

type MessageHandler = (data: unknown) => void;
type ErrorHandler = (error: Event | Error) => void;

export interface WebSocketOptions {
  url: string;
  getToken: () => Promise<string>;
  protocols?: string[];
  heartbeatIntervalMs?: number;
  maxReconnectDelayMs?: number;
  maxQueueSize?: number;
  onConnect?: () => void;
  onDisconnect?: (code: number, reason: string) => void;
}

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private state: ConnectionState = 'disconnected';
  private reconnectDelay = 1000;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private heartbeatTimer?: ReturnType<typeof setInterval>;
  private lastPongAt = 0;
  private messageQueue: unknown[] = [];
  private handlers = new Map<string, Set<MessageHandler>>();
  private errorHandlers = new Set<ErrorHandler>();

  private readonly options: Required<WebSocketOptions>;

  constructor(options: WebSocketOptions) {
    this.options = {
      protocols: [],
      heartbeatIntervalMs: 30_000,
      maxReconnectDelayMs: 30_000,
      maxQueueSize: 100,
      onConnect: () => {},
      onDisconnect: () => {},
      ...options,
    };
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  async connect(): Promise<void> {
    if (this.state === 'connected' || this.state === 'connecting') return;
    this.state = 'connecting';

    try {
      const token = await this.options.getToken();
      const url = `${this.options.url}?token=${encodeURIComponent(token)}`;

      this.ws = new WebSocket(url, this.options.protocols);
      this.attachHandlers();
    } catch (err) {
      this.state = 'disconnected';
      this.scheduleReconnect();
      throw err;
    }
  }

  disconnect(): void {
    this.clearTimers();
    if (this.ws) {
      this.ws.onclose = null; // Prevent reconnect on intentional close
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    this.state = 'disconnected';
  }

  getState(): ConnectionState {
    return this.state;
  }

  // ---------------------------------------------------------------------------
  // Messaging
  // ---------------------------------------------------------------------------

  send(type: string, payload: unknown): void {
    const message = JSON.stringify({ type, payload, ts: Date.now() });

    if (this.state === 'connected' && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(message);
    } else {
      if (this.messageQueue.length < this.options.maxQueueSize) {
        this.messageQueue.push(message);
      } else {
        console.warn('WebSocket queue full — dropping message:', type);
      }
    }
  }

  on(type: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);
    return () => this.handlers.get(type)?.delete(handler);
  }

  onError(handler: ErrorHandler): () => void {
    this.errorHandlers.add(handler);
    return () => this.errorHandlers.delete(handler);
  }

  // ---------------------------------------------------------------------------
  // Internal handlers
  // ---------------------------------------------------------------------------

  private attachHandlers(): void {
    if (!this.ws) return;

    this.ws.onopen = () => {
      this.state = 'connected';
      this.reconnectDelay = 1000;
      this.lastPongAt = Date.now();
      this.startHeartbeat();
      this.flushQueue();
      this.options.onConnect();
      this.emit('connection:state', { state: 'connected' });
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data as string) as { type: string; payload: unknown };
        if (message.type === 'pong') {
          this.lastPongAt = Date.now();
          return;
        }
        this.emit(message.type, message.payload);
        this.emit('*', message); // Wildcard handler
      } catch {
        console.error('WebSocket: failed to parse message');
      }
    };

    this.ws.onclose = (event) => {
      this.state = 'disconnected';
      this.clearTimers();
      this.options.onDisconnect(event.code, event.reason);
      this.emit('connection:state', { state: 'disconnected', code: event.code });

      // Don't reconnect on clean close
      if (event.code !== 1000 && event.code !== 1001) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (error) => {
      this.errorHandlers.forEach((h) => h(error));
      this.emit('connection:error', { error });
    };
  }

  private emit(type: string, payload?: unknown): void {
    const handlerSet = this.handlers.get(type);
    if (handlerSet) {
      handlerSet.forEach((h) => {
        try { h(payload); }
        catch (err) { console.error('WebSocket handler error:', err); }
      });
    }
  }

  private flushQueue(): void {
    while (this.messageQueue.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
      const msg = this.messageQueue.shift();
      this.ws.send(msg as string);
    }
  }

  private scheduleReconnect(): void {
    this.state = 'reconnecting';
    const delay = Math.min(this.reconnectDelay, this.options.maxReconnectDelayMs);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.options.maxReconnectDelayMs);

    console.info(`WebSocket: reconnecting in ${delay}ms`);
    this.reconnectTimer = setTimeout(() => this.connect().catch(() => {}), delay);
  }

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      if (this.state !== 'connected') return;

      const timeSincePong = Date.now() - this.lastPongAt;
      if (timeSincePong > this.options.heartbeatIntervalMs * 2) {
        console.warn('WebSocket: heartbeat timeout — forcing reconnect');
        this.ws?.close(4000, 'Heartbeat timeout');
        return;
      }

      this.ws?.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
    }, this.options.heartbeatIntervalMs);
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
  }
}
