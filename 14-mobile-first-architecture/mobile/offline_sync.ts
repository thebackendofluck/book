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
 * Chapter 7: Mobile-First Architecture for iGaming
 * Offline Sync Manager
 *
 * This module implements an offline-first architecture for the gambling PWA,
 * providing durable operation queuing via IndexedDB. It handles:
 * - Persistent sync queue for bets, withdrawals, and profile updates
 * - Automatic sync when connectivity is restored
 * - Conflict resolution for operations placed while offline
 * - Retry logic with exponential backoff (max 3 attempts)
 *
 * Reference: Chapter 7 - Offline Gaming with State Synchronization section
 */

interface SyncOperation {
  id: string;
  type: 'bet' | 'withdrawal' | 'profile_update';
  payload: any;
  timestamp: number;
  retryCount: number;
  status: 'pending' | 'syncing' | 'completed' | 'failed';
}

class OfflineSyncManager {
  private db: IndexedDB;
  private syncQueue: SyncOperation[] = [];
  private isOnline: boolean = navigator.onLine;
  private syncInProgress: boolean = false;

  constructor() {
    this.initializeDatabase();
    this.setupEventListeners();
  }

  private async initializeDatabase(): Promise<void> {
    this.db = await openDB('casino-offline', 1, {
      upgrade(db) {
        // Sync queue for pending operations
        if (!db.objectStoreNames.contains('sync_queue')) {
          const syncStore = db.createObjectStore('sync_queue', { keyPath: 'id' });
          syncStore.createIndex('timestamp', 'timestamp');
          syncStore.createIndex('status', 'status');
        }

        // Cache for game states
        if (!db.objectStoreNames.contains('game_cache')) {
          db.createObjectStore('game_cache', { keyPath: 'gameId' });
        }

        // User session data
        if (!db.objectStoreNames.contains('session')) {
          db.createObjectStore('session', { keyPath: 'userId' });
        }
      }
    });
  }

  async queueOperation(operation: Omit<SyncOperation, 'id' | 'retryCount' | 'status'>): Promise<void> {
    const fullOperation: SyncOperation = {
      ...operation,
      id: generateUUID(),
      retryCount: 0,
      status: 'pending'
    };

    // Store in IndexedDB for persistence
    await this.db.add('sync_queue', fullOperation);
    this.syncQueue.push(fullOperation);

    // Attempt sync if online
    if (this.isOnline && !this.syncInProgress) {
      this.processSyncQueue();
    }
  }

  private async processSyncQueue(): Promise<void> {
    if (this.syncInProgress || !this.isOnline) return;

    this.syncInProgress = true;

    try {
      // Get pending operations sorted by timestamp
      const pendingOps = await this.db.getAllFromIndex('sync_queue', 'status', 'pending');
      pendingOps.sort((a, b) => a.timestamp - b.timestamp);

      for (const operation of pendingOps) {
        try {
          await this.syncOperation(operation);
        } catch (error) {
          await this.handleSyncError(operation, error);
        }
      }
    } finally {
      this.syncInProgress = false;
    }
  }

  private async syncOperation(operation: SyncOperation): Promise<void> {
    // Update status to syncing
    operation.status = 'syncing';
    await this.db.put('sync_queue', operation);

    // Perform the actual sync based on operation type
    switch (operation.type) {
      case 'bet':
        await this.syncBet(operation);
        break;
      case 'withdrawal':
        await this.syncWithdrawal(operation);
        break;
      case 'profile_update':
        await this.syncProfileUpdate(operation);
        break;
      default:
        throw new Error(`Unknown operation type: ${operation.type}`);
    }

    // Mark as completed
    operation.status = 'completed';
    await this.db.put('sync_queue', operation);
  }

  private async syncBet(operation: SyncOperation): Promise<void> {
    const { gameId, amount, betData } = operation.payload;

    // Validate bet still makes sense
    const currentState = await this.getCachedGameState(gameId);
    if (!this.isBetStillValid(betData, currentState)) {
      throw new Error('Bet no longer valid due to game state changes');
    }

    // Make API call
    const response = await fetch('/api/bets', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Offline-Sync': 'true',
        'X-Original-Timestamp': operation.timestamp.toString()
      },
      body: JSON.stringify({
        gameId,
        amount,
        betData,
        offlineId: operation.id
      })
    });

    if (!response.ok) {
      throw new Error(`Bet sync failed: ${response.statusText}`);
    }

    const result = await response.json();

    // Update local state
    await this.updateCachedGameState(gameId, result.newGameState);
    await this.updateLocalBalance(result.newBalance);
  }

  private async handleSyncError(operation: SyncOperation, error: Error): Promise<void> {
    operation.retryCount++;

    if (operation.retryCount >= 3) {
      // Max retries reached, mark as failed
      operation.status = 'failed';
      await this.db.put('sync_queue', operation);

      // Notify user of sync failure
      this.notifySyncFailure(operation, error);
    } else {
      // Keep in queue for retry
      operation.status = 'pending';
      await this.db.put('sync_queue', operation);
    }
  }

  private setupEventListeners(): void {
    // Listen for online/offline events
    window.addEventListener('online', () => {
      this.isOnline = true;
      this.processSyncQueue();
    });

    window.addEventListener('offline', () => {
      this.isOnline = false;
    });

    // Periodic sync attempt
    setInterval(() => {
      if (this.isOnline && !this.syncInProgress) {
        this.processSyncQueue();
      }
    }, 30000); // Every 30 seconds
  }

  // Conflict resolution for offline operations
  private resolveConflict(
    localOperation: SyncOperation,
    serverState: any
  ): SyncOperation | null {
    // Implement conflict resolution logic based on business rules
    switch (localOperation.type) {
      case 'bet':
        // Check if game round has advanced
        if (serverState.currentRound > localOperation.payload.round) {
          // Bet is no longer valid
          return null;
        }
        return localOperation;

      case 'withdrawal':
        // Check if sufficient balance exists
        if (serverState.balance < localOperation.payload.amount) {
          // Scale down withdrawal or cancel
          localOperation.payload.amount = Math.min(
            localOperation.payload.amount,
            serverState.balance
          );
        }
        return localOperation;

      default:
        return localOperation;
    }
  }
}
