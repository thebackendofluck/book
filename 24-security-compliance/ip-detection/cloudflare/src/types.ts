// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Chapter 24 — IP Detection Pipeline
 * Core TypeScript interfaces for the 8-gate security chain.
 * All types are designed for zero-allocation hot paths.
 */

// ─── Gate result ────────────────────────────────────────────────────────────

export type GateAction = "block" | "review" | "pass";

export type ReasonCode =
  | "BANNED_PROXY_TOR"
  | "BANNED_PROXY_DC"
  | "BANNED_PROXY_VPN"
  | "BANNED_PROXY_KNOWN"
  | "BANNED_IP_BLACKLIST"
  | "HIGH_FRAUD_SCORE"
  | "DEVICE_ANOMALY"
  | "SANCTIONS_MATCH"
  | "KYC_BLOCKED"
  | "KYC_PENDING"
  | "PASS";

export interface GateResult {
  action: GateAction;
  reason: ReasonCode;
  gate: number;
  /** Human-readable detail appended to structured log — never sent to client. */
  detail?: string;
  /** Gate wall-clock duration in microseconds (populated by the runner). */
  durationUs?: number;
}

// ─── Cloudflare extensions ───────────────────────────────────────────────────

/**
 * Extends the CF request properties with all fields used by the pipeline.
 * We keep a strict interface so TypeScript catches any field name typo.
 */
export interface CFProps {
  asn?: number;
  asOrganization?: string;
  country?: string;
  isEUCountry?: string; // "1" or undefined
  city?: string;
  latitude?: string;
  longitude?: string;
  botManagement?: {
    score?: number;          // 1–99: low = bot, high = human
    verifiedBot?: boolean;
    ja3Hash?: string;
    detectionIds?: Record<string, number>;
  };
  // Cloudflare Spectrum / proxy detection fields
  isAnonymous?: string;       // "1" when CF detects anonymous proxy
  isTor?: string;             // "1" when CF detects Tor exit
  isAnonymousVpn?: string;
  isPublicProxy?: string;
}

// ─── Player request context ──────────────────────────────────────────────────

export interface PlayerRequest {
  /** Client IP (already resolved by CF — never a proxy IP). */
  ip: string;
  /** Cloudflare cf object attached to the incoming Request. */
  cf: CFProps;
  /** Optional player ID extracted from JWT / session cookie. */
  playerId?: string;
  /** Player display name — used for sanctions fuzzy match. */
  playerName?: string;
  /** Incoming JA3 fingerprint string (raw, before hashing). */
  ja3Raw?: string;
  /** User-Agent header. */
  userAgent?: string;
  /** Accept-Language header — cross-checked against country. */
  acceptLanguage?: string;
  /** ISO timestamp of request. */
  requestedAt: string;
}

// ─── Gate configuration ──────────────────────────────────────────────────────

export interface GateConfig {
  /**
   * Bot Management score threshold below which we treat the request as a bot/VPN.
   * Cloudflare default human threshold is ≥30. We use a stricter 40 for iGaming.
   */
  botScoreThreshold: number;

  /**
   * Fraud score (gate 5) above which the request is blocked outright.
   * Range 0–100. Requests above `fraudBlockThreshold` are blocked;
   * above `fraudReviewThreshold` are sent to the review queue.
   */
  fraudBlockThreshold: number;
  fraudReviewThreshold: number;

  /**
   * Velocity windows (seconds). KV TTL is set to match the longest window.
   */
  velocityWindow1mSec: number;  // 60
  velocityWindow5mSec: number;  // 300
  velocityWindow1hSec: number;  // 3600

  /**
   * Hard request-count limit per IP within the 1-minute window.
   * Counts exceeding this are scored as high-fraud regardless of other signals.
   */
  rateLimit1m: number;

  /**
   * Number of distinct JA3 hashes allowed per IP over the 1h window before
   * flagging as device anomaly.
   */
  ja3DistinctLimit: number;

  /** KV namespace TTLs (seconds). */
  blacklistTtl: number;
  velocityTtl: number;
  fingerprintTtl: number;
}

export const DEFAULT_CONFIG: GateConfig = {
  botScoreThreshold: 40,
  fraudBlockThreshold: 75,
  fraudReviewThreshold: 50,
  velocityWindow1mSec: 60,
  velocityWindow5mSec: 300,
  velocityWindow1hSec: 3600,
  rateLimit1m: 60,
  ja3DistinctLimit: 3,
  blacklistTtl: 86400,
  velocityTtl: 3600,
  fingerprintTtl: 86400,
};

// ─── Environment bindings ────────────────────────────────────────────────────

export interface Env {
  /** KV: ip -> { reason, expires } JSON. */
  IP_BLACKLIST: KVNamespace;
  /** KV: ja3Hash -> JSON array of {ip, seenAt}. */
  DEVICE_FINGERPRINTS: KVNamespace;
  /** KV: velocity:<ip>:<window> -> counter string. */
  FRAUD_VELOCITY: KVNamespace;
  /** KV: sanctions:country -> "1"; sanctions:name:<token> -> name. */
  SANCTIONS_LIST: KVNamespace;
  /** D1: player verification records. */
  PLAYER_DB: D1Database;
}

// ─── Pipeline outcome ────────────────────────────────────────────────────────

export interface PipelineResult {
  /** Final decision after all gates. */
  action: GateAction;
  reason: ReasonCode;
  /** Index of the gate that produced the final decision (1–8). */
  decisionGate: number;
  /** Per-gate results for observability (all gates up to and including the deciding one). */
  gates: GateResult[];
  /** Total pipeline duration in microseconds. */
  totalDurationUs: number;
}
