// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// ────────────────────────────────────────────────────────────────────────────
// Shared types used across all Workers in this project.
// Kept in a single file so the Durable Object class, the main classifier,
// and the admin routes all import from one canonical source.
// ────────────────────────────────────────────────────────────────────────────

export type TrafficClass = "NORMAL" | "SUSPICIOUS" | "ATTACK";

export type ResponseAction =
  | "ALLOW"
  | "RATE_LIMIT"
  | "JS_CHALLENGE"
  | "CAPTCHA"
  | "BLOCK";

export interface ClassificationResult {
  trafficClass: TrafficClass;
  action: ResponseAction;
  reasons: string[];
  rateLimitMultiplier: number;
  campaignActive: boolean;
}

export interface RateLimitCounters {
  ipPerMinute: number;
  ipPerSecond: number;
  asnPerMinute: number;
  asnPerSecond: number;
}

export interface CampaignRecord {
  geo: string;
  multiplier: number;
  startedAt: number;
  expiresAt: number;
  note?: string;
}

export interface AttackEvent {
  ip: string;
  asn: string;
  country: string;
  ja3: string;
  timestamp: number;
  reasons: string[];
  action: ResponseAction;
  ray: string;
}

export interface AsnSummaryEntry {
  asn: string;
  count: number;
  firstSeen: number;
  lastSeen: number;
  sampleIps: string[];
}

// Workers KV + Durable Object bindings surfaced by Wrangler
export interface Env {
  RATE_LIMITS: KVNamespace;
  CAMPAIGNS: KVNamespace;
  ATTACK_LOG: KVNamespace;
  JA3_BLOCKLIST: KVNamespace;
  ATTACK_COUNTER: DurableObjectNamespace;
  ORIGIN_AUTOSCALER_URL: string;
  ORIGIN_ALERT_URL: string;
}

// Thresholds (baseline — multiplied by campaign multiplier when active)
export const THRESHOLDS = {
  IP_PER_MINUTE_WARN: 120,
  IP_PER_MINUTE_ATTACK: 300,
  IP_PER_SECOND_ATTACK: 20,
  ASN_PER_MINUTE_WARN: 3_000,
  ASN_PER_MINUTE_ATTACK: 8_000,
  BOT_SCORE_SUSPICIOUS: 30,   // 0 = definitely bot, 100 = definitely human
  BOT_SCORE_ATTACK: 10,
  THREAT_SCORE_SUSPICIOUS: 10,
  THREAT_SCORE_ATTACK: 25,
  GEO_SPIKE_MULTIPLIER: 5,    // country baseline × this = suspicious
} as const;

// KV TTLs in seconds
export const TTL = {
  RATE_LIMIT_SECOND: 2,
  RATE_LIMIT_MINUTE: 70,
  BLOCKED_IP: 3_600,          // 1 hour
  GEO_BASELINE: 3_600,
  ATTACK_BATCH: 86_400,       // 24 hours
} as const;
