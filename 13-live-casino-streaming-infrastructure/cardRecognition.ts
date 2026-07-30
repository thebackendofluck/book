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
 * cardRecognition.ts — OCR-Based Card Recognition Service
 *
 * Processes camera frames from live casino tables, identifies dealt cards
 * using a CNN model, and broadcasts verified game state via Redis Pub/Sub.
 *
 * Cross-camera validation: at least 2 cameras must agree on a card identity
 * with confidence > 0.80 before the result is committed to game state.
 * A single camera with confidence > 0.85 triggers an update only if it
 * is confirming an existing card, not declaring a new one.
 *
 * Chapter 13 — Live Casino Streaming Infrastructure
 */

import Redis from 'ioredis';

interface CardResult {
  card: string;        // e.g. "AS" (Ace of Spades), "KH" (King of Hearts)
  confidence: number;  // 0.0 – 1.0
  timestamp: number;   // epoch ms
  cameraId: string;
}

interface GameState {
  tableId: string;
  cards: CardResult[];
  lastUpdated: number;
  dealerPosition?: 'left' | 'right' | 'center';
}

interface ModelPrediction {
  label: string;
  score: number;
  alternatives?: Array<{ label: string; score: number }>;
}

interface CardRecognitionModel {
  predict(frame: Buffer): Promise<ModelPrediction>;
  warmUp(): Promise<void>;
}

// ---------------------------------------------------------------------------
// Card recognition service
// ---------------------------------------------------------------------------

export class CardRecognitionService {
  private readonly redis: Redis;
  private readonly gameStates = new Map<string, CardResult[]>();
  private model!: CardRecognitionModel;

  private readonly MIN_CONFIDENCE_SINGLE = 0.85;
  private readonly MIN_CONFIDENCE_CONSENSUS = 0.80;
  private readonly MIN_CAMERAS_CONSENSUS = 2;
  private readonly MIN_CARD_CHANGE_INTERVAL_MS = 1000;

  constructor(redisClient: Redis) {
    this.redis = redisClient;
  }

  async init(model: CardRecognitionModel): Promise<void> {
    this.model = model;
    await this.model.warmUp();
  }

  // ---------------------------------------------------------------------------
  // Frame processing entry point
  // ---------------------------------------------------------------------------

  async processFrame(
    tableId: string,
    cameraId: string,
    frame: Buffer
  ): Promise<CardResult | null> {
    const preprocessed = this.preprocess(frame);
    const prediction = await this.model.predict(preprocessed);

    // Reject low-confidence predictions outright
    if (prediction.score < 0.60) {
      return null;
    }

    const result: CardResult = {
      card: prediction.label,
      confidence: prediction.score,
      timestamp: Date.now(),
      cameraId,
    };

    await this.updateGameState(tableId, result);
    return result;
  }

  // ---------------------------------------------------------------------------
  // Game state management
  // ---------------------------------------------------------------------------

  private async updateGameState(tableId: string, result: CardResult): Promise<void> {
    const cards = this.gameStates.get(tableId) ?? [];
    const existing = cards.find((c) => c.cameraId === result.cameraId);

    if (existing) {
      // Update only if: different card, high confidence, and minimum time gap
      if (
        existing.card !== result.card &&
        result.confidence > this.MIN_CONFIDENCE_SINGLE &&
        result.timestamp - existing.timestamp > this.MIN_CARD_CHANGE_INTERVAL_MS
      ) {
        Object.assign(existing, result);
      } else if (existing.card === result.card && result.confidence > existing.confidence) {
        // Same card, higher confidence — update confidence score
        existing.confidence = result.confidence;
        existing.timestamp = result.timestamp;
      }
    } else {
      cards.push(result);
      this.gameStates.set(tableId, cards);
    }

    // Cross-camera consensus validation
    const consensus = this.validateConsensus(cards);
    if (consensus) {
      await this.publishGameState(tableId, consensus);
    }
  }

  /**
   * Group by card value; require >= 2 cameras with confidence > MIN_CONFIDENCE_CONSENSUS.
   * Returns confirmed cards or null if no consensus.
   */
  private validateConsensus(cards: CardResult[]): CardResult[] | null {
    const groups = new Map<string, CardResult[]>();

    for (const c of cards) {
      if (c.confidence < this.MIN_CONFIDENCE_CONSENSUS) continue;
      const arr = groups.get(c.card) ?? [];
      arr.push(c);
      groups.set(c.card, arr);
    }

    const confirmed = [...groups.entries()]
      .filter(([, results]) => results.length >= this.MIN_CAMERAS_CONSENSUS)
      .map(([, results]) => {
        // Return the highest-confidence result for this card
        return results.reduce((best, r) => r.confidence > best.confidence ? r : best);
      });

    return confirmed.length > 0 ? confirmed : null;
  }

  private async publishGameState(tableId: string, confirmedCards: CardResult[]): Promise<void> {
    const payload = JSON.stringify({
      type: 'card_detected',
      tableId,
      cards: confirmedCards,
      ts: Date.now(),
    });

    await this.redis.publish(`table:${tableId}:updates`, payload);
  }

  // ---------------------------------------------------------------------------
  // Image preprocessing
  // ---------------------------------------------------------------------------

  /**
   * Preprocess a raw camera frame:
   *   1. Resize to model input dimensions (e.g. 224x224)
   *   2. Convert to grayscale
   *   3. Apply Gaussian blur to reduce noise
   *   4. Enhance contrast (CLAHE)
   *   5. Normalise pixel values to [0, 1]
   *
   * In production, use sharp (fast) or opencv4nodejs (full CV pipeline).
   * This implementation is a placeholder for the preprocessing chain.
   */
  private preprocess(frame: Buffer): Buffer {
    // Production: await sharp(frame).resize(224, 224).greyscale().normalize().toBuffer()
    return frame;
  }

  // ---------------------------------------------------------------------------
  // State access
  // ---------------------------------------------------------------------------

  getTableState(tableId: string): GameState | null {
    const cards = this.gameStates.get(tableId);
    if (!cards || cards.length === 0) return null;

    return {
      tableId,
      cards,
      lastUpdated: Math.max(...cards.map((c) => c.timestamp)),
    };
  }

  clearTableState(tableId: string): void {
    this.gameStates.delete(tableId);
  }

  getRecognitionStats(): Record<string, unknown> {
    const tables = [...this.gameStates.entries()];
    return {
      activeTables: tables.length,
      totalCameraFeeds: tables.reduce((sum, [, cards]) => sum + cards.length, 0),
      averageConfidence: tables.length === 0 ? 0 :
        tables.flatMap(([, cards]) => cards.map((c) => c.confidence))
          .reduce((sum, c) => sum + c, 0) /
        tables.flatMap(([, cards]) => cards).length,
    };
  }
}

// ---------------------------------------------------------------------------
// Card validation utility
// ---------------------------------------------------------------------------

/** Valid card labels: rank (2-9, T, J, Q, K, A) + suit (S, H, D, C) */
const VALID_CARDS = new Set<string>([
  ...['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
    .flatMap((rank) => ['S', 'H', 'D', 'C'].map((suit) => `${rank}${suit}`)),
]);

export function isValidCard(label: string): boolean {
  return VALID_CARDS.has(label);
}

export function cardDisplayName(label: string): string {
  const rankMap: Record<string, string> = {
    '2': '2', '3': '3', '4': '4', '5': '5', '6': '6', '7': '7', '8': '8',
    '9': '9', 'T': '10', 'J': 'Jack', 'Q': 'Queen', 'K': 'King', 'A': 'Ace',
  };
  const suitMap: Record<string, string> = {
    'S': 'Spades', 'H': 'Hearts', 'D': 'Diamonds', 'C': 'Clubs',
  };
  const rank = label.charAt(0);
  const suit = label.charAt(1);
  return `${rankMap[rank] ?? rank} of ${suitMap[suit] ?? suit}`;
}

export function blackjackValue(card: string): number {
  const rank = card.charAt(0);
  if (['T', 'J', 'Q', 'K'].includes(rank)) return 10;
  if (rank === 'A') return 11; // Caller handles Ace as 1 when bust
  return parseInt(rank, 10);
}
