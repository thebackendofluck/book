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
 * Legacy internal event definitions and validators.
 *
 * SIGAP (Sistema de Gestão de Apostas) is the Brazilian federal regulatory
 * platform operated by the Secretaria de Prêmios e Apostas (SPA/MF).
 *
 * These JSON records are internal source events only. They are not submitted
 * directly to SIGAP. The regulatory boundary receives an XSD-valid, e-CNPJ-
 * signed XML document, GZIP/Base64 packaged and delivered as a batch.
 *
 * Regulatory basis: Lei 14.790/2023, Portaria SPA/MF 827/2023.
 *
 * The production-shaped delivery code is ../sigap-reporter.ts. This module is
 * retained for compatibility with older internal event examples.
 */

import type { SigapEvent, SigapBatchReport, SigapEventType } from '../types.js';

// ── Event type registry ──────────────────────────────────────────────────────

const VALID_EVENT_TYPES = new Set<SigapEventType>([
  'bet_placed',
  'bet_settled',
  'deposit_pix',
  'withdrawal_pix',
  'session_start',
  'session_end',
  'self_exclusion',
  'kyc_approved',
]);

// ── SIGAP API path constants ─────────────────────────────────────────────────

export const SIGAP_PATHS = {
  BATCHES: '/batches',
  STATUS: '/status',
} as const;

// ── Validation ───────────────────────────────────────────────────────────────

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

/**
 * Validate one internal source event before durable storage.
 * Returns a list of human-readable error strings; empty list means valid.
 */
export function validateSigapEvent(event: unknown): ValidationResult {
  const errors: string[] = [];

  if (!event || typeof event !== 'object') {
    return { valid: false, errors: ['Event must be a non-null object'] };
  }

  const e = event as Record<string, unknown>;

  if (!e.eventId || typeof e.eventId !== 'string' || e.eventId.length === 0) {
    errors.push('eventId is required and must be a non-empty string');
  }

  if (!e.operatorId || typeof e.operatorId !== 'string') {
    errors.push('operatorId is required');
  }

  if (!VALID_EVENT_TYPES.has(e.eventType as SigapEventType)) {
    errors.push(`eventType must be one of: ${[...VALID_EVENT_TYPES].join(', ')}`);
  }

  if (!e.cpf || typeof e.cpf !== 'string' || !/^\d{11}$/.test(e.cpf as string)) {
    errors.push('cpf must be an 11-digit numeric string');
  }

  if (!e.timestamp || typeof e.timestamp !== 'string' || !isValidISO8601(e.timestamp as string)) {
    errors.push('timestamp must be a valid ISO-8601 string');
  }

  if (!e.payload || typeof e.payload !== 'object') {
    errors.push('payload must be a non-null object');
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Validate a legacy internal aggregation object; not an official SIGAP batch.
 */
export function validateSigapBatchReport(report: unknown): ValidationResult {
  const errors: string[] = [];

  if (!report || typeof report !== 'object') {
    return { valid: false, errors: ['Report must be a non-null object'] };
  }

  const r = report as Record<string, unknown>;

  if (!r.operatorId || typeof r.operatorId !== 'string') {
    errors.push('operatorId is required');
  }

  if (!r.reportDate || !/^\d{4}-\d{2}-\d{2}$/.test(r.reportDate as string)) {
    errors.push('reportDate must be in YYYY-MM-DD format');
  }

  if (!Array.isArray(r.events)) {
    errors.push('events must be an array');
  } else {
    (r.events as unknown[]).forEach((evt, idx) => {
      const result = validateSigapEvent(evt);
      result.errors.forEach(err => errors.push(`events[${idx}]: ${err}`));
    });
  }

  if (r.ggr && typeof r.ggr === 'object') {
    const ggr = r.ggr as Record<string, unknown>;
    if (typeof ggr.totalStakeCentavos !== 'number' || ggr.totalStakeCentavos < 0) {
      errors.push('ggr.totalStakeCentavos must be a non-negative number');
    }
    if (typeof ggr.totalPayoutCentavos !== 'number' || ggr.totalPayoutCentavos < 0) {
      errors.push('ggr.totalPayoutCentavos must be a non-negative number');
    }
    if (typeof ggr.ggrCentavos !== 'number') {
      errors.push('ggr.ggrCentavos must be a number');
    }
  } else {
    errors.push('ggr summary is required');
  }

  return { valid: errors.length === 0, errors };
}

// ── SIGAP event builders ──────────────────────────────────────────────────────

/** Build an internal `bet_placed` source event from a placed bet record. */
export function buildBetPlacedEvent(
  operatorId: string,
  cpf: string,
  bet: {
    id: string;
    marketId: string;
    selection: string;
    oddsAtPlacement: number;
    stakeAmountCentavos: number;
  }
): SigapEvent {
  return {
    eventId:    `bet_placed_${bet.id}_${Date.now()}`,
    operatorId,
    eventType:  'bet_placed',
    cpf,
    timestamp:  new Date().toISOString(),
    payload: {
      betId:             bet.id,
      marketId:          bet.marketId,
      selection:         bet.selection,
      odds:              bet.oddsAtPlacement,
      stakeAmountBRL:    centavosToBRL(bet.stakeAmountCentavos),
    },
  };
}

/** Build an internal `deposit_pix` source event from a confirmed Pix webhook. */
export function buildDepositPixEvent(
  operatorId: string,
  cpf: string,
  deposit: {
    txid: string;
    endToEndId: string;
    amountCentavos: number;
  }
): SigapEvent {
  return {
    eventId:    `deposit_pix_${deposit.txid}_${Date.now()}`,
    operatorId,
    eventType:  'deposit_pix',
    cpf,
    timestamp:  new Date().toISOString(),
    payload: {
      txid:          deposit.txid,
      endToEndId:    deposit.endToEndId,
      amountBRL:     centavosToBRL(deposit.amountCentavos),
    },
  };
}

/** Build a `session_start` SIGAP event. */
export function buildSessionStartEvent(
  operatorId: string,
  cpf: string,
  sessionId: string,
  country: string
): SigapEvent {
  return {
    eventId:    `session_start_${sessionId}_${Date.now()}`,
    operatorId,
    eventType:  'session_start',
    cpf,
    timestamp:  new Date().toISOString(),
    payload: {
      sessionId,
      country,
    },
  };
}

// ── SIGAP HTTP request builder ────────────────────────────────────────────────

/**
 * Build a fetch Request object for SIGAP API submission.
 *
 * mTLS certificates are attached via the `fetcher` binding in wrangler.toml,
 * not here. This function constructs the HTTP request envelope only.
 */
export function buildSigapRequest(
  baseUrl: string,
  path: string,
  body: SigapEvent | SigapBatchReport
): Request {
  return new Request(`${baseUrl}${path}`, {
    method:  'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept':        'application/json',
      'X-SIGAP-Version': '1',
    },
    body: JSON.stringify(body),
  });
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function isValidISO8601(value: string): boolean {
  return !isNaN(Date.parse(value));
}

/** Convert integer centavos to BRL decimal (e.g. 5000 → 50.00). */
function centavosToBRL(centavos: number): number {
  return centavos / 100;
}
