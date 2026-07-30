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
 * psp-router.ts
 * -------------
 * PSP (Payment Service Provider) routing with fallback logic.
 *
 * Priority order per method:
 *   1. Try primary PSP
 *   2. On error/decline, try fallback PSPs in order
 *   3. Return first successful route or final error
 *
 * All PSP calls are stubs that simulate HTTP interactions so the worker
 * compiles and deploys without real credentials.  Replace the stub
 * implementations with real fetch() calls in production.
 */

export type PaymentMethod = "card" | "bank_transfer" | "ewallet" | "crypto";
export type Currency = string; // ISO 4217

export interface PspRoute {
  name: string;
  endpoint: string;
  priority: number;
}

export interface PspConfig {
  routes: Record<PaymentMethod, PspRoute[]>;
}

export interface PspResult {
  ok: boolean;
  pspName: string;
  pspRef?: string;
  errorCode?: string;
  errorMessage?: string;
}

// ---------------------------------------------------------------------------
// Default PSP config (overridden by KV in production)
// ---------------------------------------------------------------------------

export const DEFAULT_PSP_CONFIG: PspConfig = {
  routes: {
    card: [
      { name: "stripe",    endpoint: "https://api.stripe.com/v1/payment_intents",    priority: 1 },
      { name: "adyen",     endpoint: "https://checkout-test.adyen.com/v70/payments", priority: 2 },
      { name: "worldpay",  endpoint: "https://access.worldpay.com/verifyThreeDsPayment", priority: 3 },
    ],
    bank_transfer: [
      { name: "tink",      endpoint: "https://api.tink.com/api/v1/payments/initiate", priority: 1 },
      { name: "truelayer", endpoint: "https://payment.truelayer.com/api/single-immediate-payments", priority: 2 },
    ],
    ewallet: [
      { name: "skrill",   endpoint: "https://www.skrill.com/app/pay.pl",  priority: 1 },
      { name: "neteller", endpoint: "https://api.neteller.com/v1/transferIn", priority: 2 },
    ],
    crypto: [
      { name: "coinpayments", endpoint: "https://www.coinpayments.net/apiv2/invoices/create", priority: 1 },
      { name: "bitpay",       endpoint: "https://bitpay.com/invoices", priority: 2 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Stub PSP connector — replace with real fetch() per PSP in production
// ---------------------------------------------------------------------------

async function callPsp(
  route: PspRoute,
  payload: { amount: number; currency: Currency; playerId: string },
): Promise<PspResult> {
  // Stub: simulate 95% success rate for realism in book demos
  const succeed = Math.random() > 0.05;

  if (!succeed) {
    return {
      ok: false,
      pspName: route.name,
      errorCode: "DECLINED",
      errorMessage: "Card declined by issuer",
    };
  }

  const pspRef = `${route.name.toUpperCase()}_${Date.now()}_${Math.random().toString(36).slice(2, 9).toUpperCase()}`;

  return {
    ok: true,
    pspName: route.name,
    pspRef,
  };
}

// ---------------------------------------------------------------------------
// Router — tries PSPs in priority order with automatic fallback
// ---------------------------------------------------------------------------

export async function routePayment(
  method: PaymentMethod,
  payload: { amount: number; currency: Currency; playerId: string },
  config: PspConfig = DEFAULT_PSP_CONFIG,
): Promise<PspResult> {
  const routes = (config.routes[method] ?? [])
    .slice()
    .sort((a, b) => a.priority - b.priority);

  if (routes.length === 0) {
    return {
      ok: false,
      pspName: "none",
      errorCode: "NO_PSP_AVAILABLE",
      errorMessage: `No PSP configured for payment method: ${method}`,
    };
  }

  let lastResult: PspResult | undefined;

  for (const route of routes) {
    try {
      const result = await callPsp(route, payload);
      if (result.ok) return result;
      lastResult = result;
    } catch (err) {
      lastResult = {
        ok: false,
        pspName: route.name,
        errorCode: "PSP_ERROR",
        errorMessage: err instanceof Error ? err.message : "Unknown PSP error",
      };
    }
  }

  return lastResult ?? {
    ok: false,
    pspName: "none",
    errorCode: "ALL_PSP_FAILED",
    errorMessage: "All PSPs in the routing chain failed",
  };
}

// ---------------------------------------------------------------------------
// Load PSP config from KV (called on first request, cached in memory)
// ---------------------------------------------------------------------------

export async function loadPspConfig(
  kv: KVNamespace,
): Promise<PspConfig> {
  try {
    const raw = await kv.get("psp_config", "json");
    if (raw) return raw as PspConfig;
  } catch {
    // Fall through to default
  }
  return DEFAULT_PSP_CONFIG;
}
