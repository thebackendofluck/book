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
 * offlineSyncManager.ts — Offline State Synchronisation Manager
 *
 * Queues mutations (bet placements, game actions) made while offline
 * and replays them when connectivity is restored. Uses idempotency keys
 * to prevent duplicate submissions.
 *
 * Conflict resolution: last-write-wins for account state; sequence numbers
 * for game events (server is authoritative on game outcomes).
 *
 * Chapter 14 — Mobile-First Architecture for iGaming
 */

export interface QueuedOperation {
  id: string;             // Idempotency key (UUID)
  type: string;           // e.g. 'place_bet', 'cash_out', 'update_limits'
  payload: unknown;
  endpoint: string;
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  createdAt: number;
  attempts: number;
  maxAttempts: number;
  expiresAt?: number;     // Some operations expire (e.g. in-play bets)
}

export interface SyncResult {
  operationId: string;
  success: boolean;
  statusCode?: number;
  response?: unknown;
  error?: string;
}

const STORAGE_KEY = 'casino_offline_queue';
const MAX_QUEUE_SIZE = 50;
const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_EXPIRY_MS = 30 * 60 * 1000; // 30 minutes

export class OfflineSyncManager {
  private queue: QueuedOperation[] = [];
  private isSyncing = false;
  private onlineListener: (() => void) | null = null;

  constructor(
    private readonly apiBaseUrl: string,
    private readonly getAuthHeaders: () => Promise<Record<string, string>>
  ) {
    this.loadQueue();
    this.setupOnlineListener();
  }

  // ---------------------------------------------------------------------------
  // Queue management
  // ---------------------------------------------------------------------------

  enqueue(operation: Omit<QueuedOperation, 'id' | 'attempts' | 'createdAt'>): string {
    if (this.queue.length >= MAX_QUEUE_SIZE) {
      throw new Error('Offline queue is full. Please try again when connected.');
    }

    const id = this.generateIdempotencyKey();
    const op: QueuedOperation = {
      ...operation,
      id,
      attempts: 0,
      createdAt: Date.now(),
      maxAttempts: operation.maxAttempts ?? DEFAULT_MAX_ATTEMPTS,
      expiresAt: operation.expiresAt ?? Date.now() + DEFAULT_EXPIRY_MS,
    };

    this.queue.push(op);
    this.saveQueue();
    return id;
  }

  dequeue(operationId: string): boolean {
    const idx = this.queue.findIndex((op) => op.id === operationId);
    if (idx === -1) return false;
    this.queue.splice(idx, 1);
    this.saveQueue();
    return true;
  }

  getQueue(): QueuedOperation[] {
    return [...this.queue];
  }

  getQueueSize(): number {
    return this.queue.length;
  }

  clearExpired(): number {
    const now = Date.now();
    const initial = this.queue.length;
    this.queue = this.queue.filter((op) => !op.expiresAt || op.expiresAt > now);
    this.saveQueue();
    return initial - this.queue.length;
  }

  // ---------------------------------------------------------------------------
  // Sync
  // ---------------------------------------------------------------------------

  async sync(): Promise<SyncResult[]> {
    if (this.isSyncing || !navigator.onLine) return [];

    this.isSyncing = true;
    this.clearExpired();

    const results: SyncResult[] = [];

    for (const op of [...this.queue]) {
      const result = await this.executeOperation(op);
      results.push(result);

      if (result.success || op.attempts >= op.maxAttempts) {
        this.dequeue(op.id);
      }
    }

    this.isSyncing = false;
    return results;
  }

  // ---------------------------------------------------------------------------
  // Internal
  // ---------------------------------------------------------------------------

  private async executeOperation(op: QueuedOperation): Promise<SyncResult> {
    op.attempts++;
    this.saveQueue();

    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}${op.endpoint}`, {
        method: op.method,
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': op.id,
          ...headers,
        },
        body: op.method !== 'DELETE' ? JSON.stringify(op.payload) : undefined,
      });

      const responseData = await response.json().catch(() => null);

      return {
        operationId: op.id,
        success: response.ok,
        statusCode: response.status,
        response: responseData,
      };
    } catch (err) {
      return {
        operationId: op.id,
        success: false,
        error: err instanceof Error ? err.message : 'Network error',
      };
    }
  }

  private setupOnlineListener(): void {
    this.onlineListener = () => {
      if (this.queue.length > 0) {
        this.sync().catch(console.error);
      }
    };
    window.addEventListener('online', this.onlineListener);
  }

  private loadQueue(): void {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      this.queue = stored ? (JSON.parse(stored) as QueuedOperation[]) : [];
    } catch {
      this.queue = [];
    }
  }

  private saveQueue(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.queue));
    } catch {
      console.warn('Failed to persist offline queue');
    }
  }

  private generateIdempotencyKey(): string {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }

  destroy(): void {
    if (this.onlineListener) {
      window.removeEventListener('online', this.onlineListener);
    }
  }
}
