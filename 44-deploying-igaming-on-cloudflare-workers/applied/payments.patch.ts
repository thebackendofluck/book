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
 * Patch overlay for new-platform/cloudflare-acmetocasino/src/payments.ts.
 *
 * This file is NOT imported by prod. It demonstrates the exact diff to apply to
 * `handleDepositIntent` and `handleWithdrawalRequest` so that they:
 *
 *   1. Reject requests missing the `Idempotency-Key` header.
 *   2. Wrap the existing handler body via `withIdempotency(...)`.
 *   3. Derive the stored `reference_id` from the idempotency key so retries
 *      resolve to the same transaction row instead of creating a fresh one.
 *
 * The current prod generator on line ~540 is:
 *
 *     const referenceId = `dep-${Date.now()}-${user.id}-${Math.random().toString(36).slice(2, 7)}`;
 *
 * With idempotency enabled, the wrapper short-circuits before we reach that
 * line on a retry, so the generator stays as-is for the first call only.
 */

import { withIdempotency, pathRequiresKey } from "./idempotency";

// Illustrative types; production imports from the real payments.ts context.
type Env = { DB: D1Database };
type AuthenticatedUser = { id: string };

declare function originalHandleDepositIntent(
  request: Request,
  env: Env,
  user: AuthenticatedUser,
  body: string,
): Promise<Response>;

declare function originalHandleWithdrawalRequest(
  request: Request,
  env: Env,
  user: AuthenticatedUser,
  body: string,
): Promise<Response>;

export async function handleDepositIntent(
  request: Request,
  env: Env,
  user: AuthenticatedUser,
): Promise<Response> {
  return withIdempotency(request, env.DB, user.id, (body) =>
    originalHandleDepositIntent(request, env, user, body),
  );
}

export async function handleWithdrawalRequest(
  request: Request,
  env: Env,
  user: AuthenticatedUser,
): Promise<Response> {
  return withIdempotency(request, env.DB, user.id, (body) =>
    originalHandleWithdrawalRequest(request, env, user, body),
  );
}

// Optional: sanity utility the router can use to pre-reject.
export function rejectIfMissingKey(request: Request): Response | null {
  const url = new URL(request.url);
  if (!pathRequiresKey(url.pathname)) return null;
  if (!request.headers.get("idempotency-key")) {
    return new Response(
      JSON.stringify({ error: "idempotency_key_required" }),
      { status: 400, headers: { "content-type": "application/json" } },
    );
  }
  return null;
}
