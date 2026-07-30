// Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * AcmeToCasino Platform - D1 Database Models
 *
 * TypeScript interfaces that mirror the D1/SQLite schema defined in schema.sql.
 * Each interface corresponds to one table. All optional fields reflect nullable
 * columns in the schema.
 *
 * These types are shared across auth.ts, games.ts, wallet.ts, kyc.ts,
 * compliance.ts, and payments.ts so that query result shapes are consistent.
 *
 * Naming convention:
 *   - Raw database row types end in "Row" (snake_case columns from D1)
 *   - Public API shapes end in "Record" or "Summary" (camelCase for JSON)
 */

// ─── users ──────────────────────────────────────────────────────────────────

export type UserStatus = 'active' | 'inactive' | 'suspended' | 'self_excluded';
export type UserRole = 'player' | 'vip' | 'staff' | 'admin';

export interface UserRow {
  id: number;
  email: string;
  username: string;
  password_hash: string;
  first_name: string | null;
  last_name: string | null;
  date_of_birth: string | null;    // ISO-8601 date string: YYYY-MM-DD
  country: string | null;          // ISO 3166-1 alpha-2
  currency: string;                // ISO 4217
  language: string;                // BCP 47
  status: UserStatus;
  balance: number;
  role: UserRole;
  created_at: string;              // ISO-8601 datetime
  updated_at: string;
}

/** Public-facing user representation — omits password_hash and internal fields */
export interface UserRecord {
  id: number;
  email: string;
  username: string;
  firstName: string | null;
  lastName: string | null;
  dateOfBirth: string | null;
  country: string | null;
  currency: string;
  language: string;
  status: UserStatus;
  balance: number;
  role: UserRole;
  createdAt: string;
}

export function toUserRecord(row: UserRow): UserRecord {
  return {
    id: row.id,
    email: row.email,
    username: row.username,
    firstName: row.first_name,
    lastName: row.last_name,
    dateOfBirth: row.date_of_birth,
    country: row.country,
    currency: row.currency,
    language: row.language,
    status: row.status,
    balance: row.balance,
    role: row.role,
    createdAt: row.created_at,
  };
}

// ─── games ──────────────────────────────────────────────────────────────────

export type GameType = 'slots' | 'table' | 'live' | 'instant';

export interface GameRow {
  id: number;
  game_id: string;
  provider: string;
  name: string;
  category: string;
  type: GameType;
  rtp: number | null;
  mobile_compatible: number;       // SQLite boolean: 0 | 1
  jurisdictions: string | null;    // JSON array of ISO country codes; null = all
  currencies: string | null;       // JSON array of ISO currency codes; null = all
  thumbnail_url: string | null;
  is_active: number;               // SQLite boolean: 0 | 1
  created_at: string;
}

export interface GameRecord {
  id: number;
  gameId: string;
  provider: string;
  name: string;
  category: string;
  type: GameType;
  rtp: number | null;
  mobileCompatible: boolean;
  jurisdictions: string[] | null;
  currencies: string[] | null;
  thumbnailUrl: string | null;
  isActive: boolean;
  createdAt: string;
}

export function toGameRecord(row: GameRow): GameRecord {
  return {
    id: row.id,
    gameId: row.game_id,
    provider: row.provider,
    name: row.name,
    category: row.category,
    type: row.type,
    rtp: row.rtp,
    mobileCompatible: row.mobile_compatible === 1,
    jurisdictions: row.jurisdictions ? (JSON.parse(row.jurisdictions) as string[]) : null,
    currencies: row.currencies ? (JSON.parse(row.currencies) as string[]) : null,
    thumbnailUrl: row.thumbnail_url,
    isActive: row.is_active === 1,
    createdAt: row.created_at,
  };
}

// ─── transactions ────────────────────────────────────────────────────────────

export type TransactionType = 'deposit' | 'withdrawal' | 'bonus' | 'wager' | 'win';
export type TransactionStatus = 'pending' | 'completed' | 'failed' | 'cancelled';

export interface TransactionRow {
  id: number;
  user_id: number;
  type: TransactionType;
  amount: number;
  currency: string;
  status: TransactionStatus;
  payment_method: string | null;
  reference_id: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface TransactionRecord {
  id: number;
  userId: number;
  type: TransactionType;
  amount: number;
  currency: string;
  status: TransactionStatus;
  paymentMethod: string | null;
  referenceId: string | null;
  createdAt: string;
  processedAt: string | null;
}

export function toTransactionRecord(row: TransactionRow): TransactionRecord {
  return {
    id: row.id,
    userId: row.user_id,
    type: row.type,
    amount: row.amount,
    currency: row.currency,
    status: row.status,
    paymentMethod: row.payment_method,
    referenceId: row.reference_id,
    createdAt: row.created_at,
    processedAt: row.processed_at,
  };
}

// ─── bonuses ─────────────────────────────────────────────────────────────────

export type BonusStatus = 'active' | 'completed' | 'expired' | 'cancelled';

export interface BonusRow {
  id: number;
  user_id: number;
  bonus_type: string;
  amount: number;
  wagering_requirement: number;
  wagering_contribution: number;
  expiry_date: string | null;
  status: BonusStatus;
  created_at: string;
}

export interface BonusRecord {
  id: number;
  userId: number;
  bonusType: string;
  amount: number;
  wageringRequirement: number;
  wageringContribution: number;
  expiryDate: string | null;
  status: BonusStatus;
  createdAt: string;
}

export function toBonusRecord(row: BonusRow): BonusRecord {
  return {
    id: row.id,
    userId: row.user_id,
    bonusType: row.bonus_type,
    amount: row.amount,
    wageringRequirement: row.wagering_requirement,
    wageringContribution: row.wagering_contribution,
    expiryDate: row.expiry_date,
    status: row.status,
    createdAt: row.created_at,
  };
}

// ─── kyc_records ─────────────────────────────────────────────────────────────

export type KycLevel = 'basic' | 'standard' | 'enhanced';
export type KycStatus = 'not_started' | 'pending' | 'approved' | 'rejected' | 'expired';
export type KycDocumentType =
  | 'passport'
  | 'national_id'
  | 'drivers_license'
  | 'proof_of_address'
  | 'source_of_funds';

export interface KycRow {
  id: number;
  user_id: number;
  level: KycLevel;
  status: KycStatus;
  document_type: KycDocumentType | null;
  document_ref: string | null;    // R2 object key — never the document content
  reviewer_notes: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface KycRecord {
  id: number;
  userId: number;
  level: KycLevel;
  status: KycStatus;
  documentType: KycDocumentType | null;
  submittedAt: string | null;
  reviewedAt: string | null;
  expiresAt: string | null;
  createdAt: string;
}

export function toKycRecord(row: KycRow): KycRecord {
  return {
    id: row.id,
    userId: row.user_id,
    level: row.level,
    status: row.status,
    documentType: row.document_type,
    submittedAt: row.submitted_at,
    reviewedAt: row.reviewed_at,
    expiresAt: row.expires_at,
    createdAt: row.created_at,
  };
}

// ─── responsible_gambling_settings ───────────────────────────────────────────

export interface ResponsibleGamblingRow {
  user_id: number;
  daily_deposit_limit: number | null;
  weekly_deposit_limit: number | null;
  monthly_deposit_limit: number | null;
  session_reminder_minutes: number | null;
  reality_check_minutes: number | null;
  self_exclusion_until: string | null;
  cool_off_until: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResponsibleGamblingRecord {
  userId: number;
  dailyDepositLimit: number | null;
  weeklyDepositLimit: number | null;
  monthlyDepositLimit: number | null;
  sessionReminderMinutes: number | null;
  realityCheckMinutes: number | null;
  selfExclusionUntil: string | null;
  coolOffUntil: string | null;
  updatedAt: string;
}

export function toResponsibleGamblingRecord(
  row: ResponsibleGamblingRow
): ResponsibleGamblingRecord {
  return {
    userId: row.user_id,
    dailyDepositLimit: row.daily_deposit_limit,
    weeklyDepositLimit: row.weekly_deposit_limit,
    monthlyDepositLimit: row.monthly_deposit_limit,
    sessionReminderMinutes: row.session_reminder_minutes,
    realityCheckMinutes: row.reality_check_minutes,
    selfExclusionUntil: row.self_exclusion_until,
    coolOffUntil: row.cool_off_until,
    updatedAt: row.updated_at,
  };
}

// ─── compliance_events ───────────────────────────────────────────────────────

export interface ComplianceEventRow {
  id: number;
  user_id: number;
  event_type: string;
  details: string | null;   // JSON blob
  created_at: string;
}

export interface ComplianceEventRecord {
  id: number;
  userId: number;
  eventType: string;
  details: Record<string, unknown> | null;
  createdAt: string;
}

export function toComplianceEventRecord(row: ComplianceEventRow): ComplianceEventRecord {
  return {
    id: row.id,
    userId: row.user_id,
    eventType: row.event_type,
    details: row.details ? (JSON.parse(row.details) as Record<string, unknown>) : null,
    createdAt: row.created_at,
  };
}

// ─── security_events ─────────────────────────────────────────────────────────

export interface SecurityEventRow {
  id: number;
  ip: string | null;
  event_type: string;
  details: string | null;   // JSON blob
  severity: number;         // 1=info, 2=warning, 3=critical
  created_at: string;
}

export interface SecurityEventRecord {
  id: number;
  ip: string | null;
  eventType: string;
  details: Record<string, unknown> | null;
  severity: number;
  createdAt: string;
}

export function toSecurityEventRecord(row: SecurityEventRow): SecurityEventRecord {
  return {
    id: row.id,
    ip: row.ip,
    eventType: row.event_type,
    details: row.details ? (JSON.parse(row.details) as Record<string, unknown>) : null,
    severity: row.severity,
    createdAt: row.created_at,
  };
}

// ─── sessions (KV value shape) ────────────────────────────────────────────────

/**
 * Shape of the value stored in the SESSIONS KV namespace.
 * Key: `session:<userId>`
 * TTL: 30 days (matches refresh token expiry)
 */
export interface SessionKVValue {
  refreshToken: string;
  userId: number;
  email: string;
  role: UserRole;
  issuedAt: string;
}

// ─── D1 query helpers ─────────────────────────────────────────────────────────

/**
 * Wraps D1's meta.last_row_id pattern for SQLite INSERT.
 * D1 does not support INSERT ... RETURNING on older compatibility dates;
 * use this helper after every INSERT that needs the new row ID.
 */
export function getInsertedId(meta: { last_row_id?: number }): number {
  if (!meta.last_row_id) throw new Error('INSERT did not return a row ID');
  return meta.last_row_id;
}

/**
 * Pagination helper — converts cursor-style offset/limit URL params
 * into a D1-compatible LIMIT/OFFSET clause.
 */
export interface PaginationParams {
  limit: number;
  offset: number;
}

export function parsePagination(url: URL, maxLimit = 100): PaginationParams {
  const limit = Math.min(
    Math.max(1, parseInt(url.searchParams.get('limit') ?? '20', 10)),
    maxLimit
  );
  const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10));
  return { limit, offset };
}
