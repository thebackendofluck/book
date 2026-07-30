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
 * auth.ts
 * -------
 * JWT verification and Role-Based Access Control (RBAC) for the backoffice API.
 *
 * Roles (least → most privileged):
 *   customer_support — player search, read-only profile
 *   kyc_analyst      — customer_support + KYC queue management
 *   payments_team    — kyc_analyst + withdrawal queue management
 *   compliance       — payments_team + audit log access, SAR/AML views
 *   superadmin       — full access to all endpoints
 *
 * JWT format (HS256, symmetric — replace with RS256/JWKS in production):
 *   { sub: playerId, role: Role, iat: number, exp: number }
 *
 * Usage:
 *   import { requireAuth } from "./auth";
 *   app.use("/api/admin/*", requireAuth(["kyc_analyst", "superadmin"]));
 */

import type { Context, MiddlewareHandler, Next } from "hono";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type Role =
  | "customer_support"
  | "kyc_analyst"
  | "payments_team"
  | "compliance"
  | "superadmin";

export interface JwtPayload {
  sub: string;   // operator staff user ID
  role: Role;
  iat: number;
  exp: number;
  name?: string;
}

export interface AuthContext {
  userId: string;
  role: Role;
  name?: string;
}

// ---------------------------------------------------------------------------
// Role hierarchy — each role inherits all permissions of roles below it
// ---------------------------------------------------------------------------

const ROLE_HIERARCHY: Record<Role, number> = {
  customer_support: 1,
  kyc_analyst:      2,
  payments_team:    3,
  compliance:       4,
  superadmin:       5,
};

export function hasMinimumRole(userRole: Role, requiredRole: Role): boolean {
  return ROLE_HIERARCHY[userRole] >= ROLE_HIERARCHY[requiredRole];
}

export function hasAnyRole(userRole: Role, allowedRoles: Role[]): boolean {
  return allowedRoles.some(
    (required) => ROLE_HIERARCHY[userRole] >= ROLE_HIERARCHY[required],
  );
}

// ---------------------------------------------------------------------------
// Minimal HS256 JWT verification using Web Crypto API (no external deps)
// ---------------------------------------------------------------------------

function base64UrlDecode(input: string): Uint8Array {
  const base64 = input.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function verifyHs256(
  token: string,
  secret: string,
): Promise<JwtPayload | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  const [headerB64, payloadB64, sigB64] = parts;

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );

  const data = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
  const sig = base64UrlDecode(sigB64);

  const valid = await crypto.subtle.verify("HMAC", key, sig, data);
  if (!valid) return null;

  try {
    const payloadJson = new TextDecoder().decode(base64UrlDecode(payloadB64));
    const payload = JSON.parse(payloadJson) as JwtPayload;

    // Check expiry
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;

    return payload;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Middleware factory
// ---------------------------------------------------------------------------

export function requireAuth(
  allowedRoles: Role[],
  jwtSecretEnvKey = "JWT_SECRET",
): MiddlewareHandler {
  return async (
    c: Context<{ Bindings: Record<string, string | undefined> }>,
    next: Next,
  ) => {
    const authHeader = c.req.header("Authorization");
    if (!authHeader?.startsWith("Bearer ")) {
      return c.json({ error: "Missing or malformed Authorization header" }, 401);
    }

    const token = authHeader.slice(7);
    const secret = c.env[jwtSecretEnvKey] ?? "dev-secret-replace-in-production";

    const payload = await verifyHs256(token, secret);
    if (!payload) {
      return c.json({ error: "Invalid or expired token" }, 401);
    }

    if (!hasAnyRole(payload.role, allowedRoles)) {
      return c.json(
        {
          error: "Insufficient permissions",
          required: allowedRoles,
          actual: payload.role,
        },
        403,
      );
    }

    // Attach auth context for downstream handlers
    c.set("auth", {
      userId: payload.sub,
      role: payload.role,
      name: payload.name,
    } satisfies AuthContext);

    await next();
  };
}

// ---------------------------------------------------------------------------
// Test token generator (dev only — never expose in production)
// ---------------------------------------------------------------------------

export async function generateDevToken(
  userId: string,
  role: Role,
  secret = "dev-secret-replace-in-production",
  expiresInSeconds = 3600,
): Promise<string> {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");

  const now = Math.floor(Date.now() / 1000);
  const payload = btoa(
    JSON.stringify({ sub: userId, role, iat: now, exp: now + expiresInSeconds }),
  ).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${header}.${payload}`),
  );

  const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");

  return `${header}.${payload}.${sigB64}`;
}
