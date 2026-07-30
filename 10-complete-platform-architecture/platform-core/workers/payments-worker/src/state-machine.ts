// Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * state-machine.ts
 * ----------------
 * Payment state transitions for iGaming deposit and withdrawal flows.
 *
 * State model:
 *   PENDING → PROCESSING → COMPLETED | FAILED | CANCELLED
 *   COMPLETED → REFUNDED (for chargebacks)
 *
 * All transitions are validated before application; invalid transitions
 * return an error result so the caller can handle idempotent retries.
 */

export type PaymentStatus =
  | "PENDING"
  | "PROCESSING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "REFUNDED";

export type PaymentType = "DEPOSIT" | "WITHDRAWAL";

export interface Payment {
  id: string;
  playerId: string;
  type: PaymentType;
  status: PaymentStatus;
  amount: number;       // minor units (e.g. pence / cents)
  currency: string;     // ISO 4217
  pspRef?: string;
  pspName?: string;
  failureReason?: string;
  createdAt: number;
  updatedAt: number;
  metadata?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Allowed transitions
// ---------------------------------------------------------------------------

const ALLOWED_TRANSITIONS: Record<PaymentStatus, PaymentStatus[]> = {
  PENDING:    ["PROCESSING", "CANCELLED", "FAILED"],
  PROCESSING: ["COMPLETED", "FAILED", "CANCELLED"],
  COMPLETED:  ["REFUNDED"],
  FAILED:     [],
  CANCELLED:  [],
  REFUNDED:   [],
};

export type TransitionResult =
  | { ok: true; payment: Payment }
  | { ok: false; error: string };

/**
 * Attempt a status transition.  Returns the updated payment on success
 * or an error description on invalid transitions.
 */
export function transition(
  payment: Payment,
  toStatus: PaymentStatus,
  meta?: { pspRef?: string; failureReason?: string },
): TransitionResult {
  const allowed = ALLOWED_TRANSITIONS[payment.status];

  if (!allowed.includes(toStatus)) {
    return {
      ok: false,
      error: `Invalid transition: ${payment.status} → ${toStatus} for payment ${payment.id}`,
    };
  }

  const updated: Payment = {
    ...payment,
    status: toStatus,
    updatedAt: Date.now(),
    ...(meta?.pspRef ? { pspRef: meta.pspRef } : {}),
    ...(meta?.failureReason ? { failureReason: meta.failureReason } : {}),
  };

  return { ok: true, payment: updated };
}

/**
 * Create a new payment in PENDING state.
 */
export function createPayment(params: {
  id: string;
  playerId: string;
  type: PaymentType;
  amount: number;
  currency: string;
  metadata?: Record<string, string>;
}): Payment {
  const now = Date.now();
  return {
    ...params,
    status: "PENDING",
    createdAt: now,
    updatedAt: now,
  };
}

/**
 * Determines whether a payment is in a terminal state.
 */
export function isTerminal(payment: Payment): boolean {
  return ["COMPLETED", "FAILED", "CANCELLED", "REFUNDED"].includes(payment.status);
}
