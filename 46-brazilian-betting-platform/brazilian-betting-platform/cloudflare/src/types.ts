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
 * Brazilian Betting Platform — Shared TypeScript Types
 *
 * All Workers in this platform import their type contracts from this module.
 * No business logic lives here — only interfaces, enums, and type aliases.
 */

// ── Worker Environment Bindings ──────────────────────────────────────────────

/** Bindings available to the API Gateway Worker. */
export interface Env {
  // D1 relational database
  DB: D1Database;

  // KV namespaces
  PLAYER_SESSIONS: KVNamespace;
  ODDS_CACHE: KVNamespace;
  RATE_LIMITS: KVNamespace;

  // R2 object storage
  KYC_DOCUMENTS: R2Bucket;

  // Durable Objects
  BETTING_SESSION: DurableObjectNamespace;

  // Service bindings
  PIX_WEBHOOK_SVC: Fetcher;
  SIGAP_REPORTER_SVC: Fetcher;
  ODDS_FEED_SVC: Fetcher;

  // Outbound authenticated transports
  SIGAP_MTLS: Fetcher;

  // Durable regulatory delivery transport
  SIGAP_BATCH_QUEUE: Queue;

  // Secrets / vars
  JWT_SECRET: string;
  JWT_ISSUER: string;
  PIX_HMAC_SECRET: string;   // inbound only: validate PSP webhook signatures
  PIX_PSP_API_KEY: string;   // outbound only: bearer credential for PSP API calls
  SIGAP_API_URL: string;
  SIGAP_OPERATOR_ID: string;
  SIGAP_BEARER_TOKEN: string;
  PIX_PSP_BASE_URL: string;
  AWS_CORE_API_URL: string;
  AWS_CORE_HMAC_SECRET: string;
  ODDS_PUBLISHER_HMAC_SECRET: string;
  /**
   * HMAC-SHA256 secret shared ONLY between the API Gateway and the PIX Webhook
   * Worker. Authenticates internal gateway→pix-webhook calls (e.g. /qrcode) so
   * the PIX charge endpoint cannot be invoked directly by external clients.
   */
  GATEWAY_INTERNAL_HMAC_SECRET: string;
  ENCRYPTION_KEY: string;
  ENVIRONMENT: string;
  PLATFORM_NAME: string;
}

// ── API Response Envelope ────────────────────────────────────────────────────

export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  errors?: string[];
  requestId?: string;
}

// ── Player / Identity ────────────────────────────────────────────────────────

/** Brazilian CPF number (11 digits, no punctuation). */
export type CPF = string;

/** ISO-8601 timestamp string. */
export type ISODateTime = string;

export interface Player {
  id: string;
  cpf: CPF;
  fullName: string;
  email: string;
  phone: string;
  /** Date of birth — ISO 8601 date only, e.g. "1990-05-20". */
  dateOfBirth: string;
  status: PlayerStatus;
  kycStatus: KycStatus;
  responsibleGambling: ResponsibleGamblingSettings;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

export type PlayerStatus = 'active' | 'suspended' | 'self_excluded' | 'closed';

export type KycStatus = 'pending' | 'submitted' | 'approved' | 'rejected';

export interface ResponsibleGamblingSettings {
  depositLimitDaily?: number;
  depositLimitWeekly?: number;
  depositLimitMonthly?: number;
  coolOffUntil?: ISODateTime;
  selfExcludedUntil?: ISODateTime;
}

// ── JWT ──────────────────────────────────────────────────────────────────────

export interface JWTPayload {
  sub: string;    // player UUID
  cpf: CPF;
  email: string;
  role: 'player' | 'operator' | 'admin';
  exp: number;
  iat: number;
  iss: string;
  /** Cloudflare PoP where the token was issued — for audit. */
  colo?: string;
}

// ── Session ──────────────────────────────────────────────────────────────────

export type SessionStatus = 'active' | 'reverifying' | 'expired';

export interface PlayerSession {
  playerId: string;
  cpf: CPF;
  country: string;
  state: SessionStatus;
  createdAt: number;         // Unix epoch ms
  lastActivity: number;      // Unix epoch ms
  reverifyAt: number;        // Unix epoch ms (30-min rolling)
  operatorId: string;
  deviceFingerprint?: string;
}

// ── Wallet ───────────────────────────────────────────────────────────────────

export type WalletOperation = 'deposit' | 'withdraw' | 'bet' | 'win' | 'refund' | 'bonus';

export interface WalletTransaction {
  id: string;
  playerId: string;
  operation: WalletOperation;
  /** Amount in BRL centavos (integer) to avoid floating-point errors. */
  amountCentavos: number;
  balanceAfterCentavos: number;
  reference: string;
  metadata?: Record<string, string>;
  createdAt: ISODateTime;
}

export interface WalletState {
  playerId: string;
  balanceCentavos: number;
  reservedCentavos: number;
  updatedAt: ISODateTime;
}

// ── PIX ──────────────────────────────────────────────────────────────────────

export type PixStatus = 'pending' | 'confirmed' | 'failed' | 'refunded';

export interface PixWebhookPayload {
  /** Unique end-to-end ID from BACEN/PIX spec (E + 32 chars). */
  endToEndId: string;
  txid: string;
  status: PixStatus;
  /** Value in BRL decimal string, e.g. "50.00". */
  valor: string;
  /** Payer CPF (masked). */
  cpfPagador: string;
  /** ISO-8601 timestamp of BACEN settlement. */
  horario: ISODateTime;
  infoPagador?: string;
}

export interface PixDepositRequest {
  playerId: string;
  amountBRL: number;
  pixKey?: string;
}

export interface PixQRCode {
  txid: string;
  qrCodeBase64: string;
  pixCopyPaste: string;
  expiresAt: ISODateTime;
  amountBRL: number;
}

// ── Odds / Sports ─────────────────────────────────────────────────────────────

export type SportCategory =
  | 'brasileirao-serie-a'
  | 'brasileirao-serie-b'
  | 'copa-do-brasil'
  | 'libertadores'
  | 'sul-americana'
  | 'nba'
  | 'nfl'
  | 'ufc-mma'
  | 'tennis'
  | 'other';

export interface OddsMarket {
  id: string;
  eventId: string;
  sport: SportCategory;
  homeTeam: string;
  awayTeam: string;
  startTime: ISODateTime;
  odds: {
    home: number;
    draw?: number;
    away: number;
  };
  suspended: boolean;
  suspendedReason?: 'sigap_integrity_alert' | 'pre_match' | 'admin';
  updatedAt: ISODateTime;
}

// ── Bets ─────────────────────────────────────────────────────────────────────

export type BetStatus = 'placed' | 'won' | 'lost' | 'voided' | 'pending_settlement';

export interface Bet {
  id: string;
  playerId: string;
  marketId: string;
  selection: 'home' | 'draw' | 'away';
  oddsAtPlacement: number;
  stakeAmountCentavos: number;
  potentialReturnCentavos: number;
  status: BetStatus;
  settledAt?: ISODateTime;
  createdAt: ISODateTime;
}

// ── SIGAP Regulatory ─────────────────────────────────────────────────────────

export type SigapEventType =
  | 'bet_placed'
  | 'bet_settled'
  | 'deposit_pix'
  | 'withdrawal_pix'
  | 'session_start'
  | 'session_end'
  | 'self_exclusion'
  | 'kyc_approved';

export interface SigapEvent {
  eventId: string;
  operatorId: string;
  eventType: SigapEventType;
  cpf: CPF;
  timestamp: ISODateTime;
  payload: Record<string, unknown>;
}

export interface SigapBatchReport {
  operatorId: string;
  reportDate: string;  // YYYY-MM-DD
  generatedAt: ISODateTime;
  events: SigapEvent[];
  ggr: {
    totalStakeCentavos: number;
    totalPayoutCentavos: number;
    ggrCentavos: number;
  };
}

// ── Geolocation ──────────────────────────────────────────────────────────────

export interface GeoVerification {
  country: string;
  region?: string;
  city?: string;
  latitude?: number;
  longitude?: number;
  verifiedAt: ISODateTime;
}

// ── Rate Limiting ────────────────────────────────────────────────────────────

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  retryAfter: number;
  limit: number;
}

// ── Cloudflare request augmentation ──────────────────────────────────────────

/** Cloudflare-specific properties on the IncomingRequest cf object. */
export interface CfProperties {
  country?: string;
  region?: string;
  city?: string;
  latitude?: string;
  longitude?: string;
  colo?: string;
  timezone?: string;
  postalCode?: string;
  threatScore?: number;
  botManagement?: {
    score: number;
    verifiedBot: boolean;
  };
}

export type BrazilRequest = Request & { cf?: CfProperties };
