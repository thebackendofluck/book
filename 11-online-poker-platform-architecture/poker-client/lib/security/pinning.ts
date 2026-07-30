// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Certificate pinning, file integrity, and update-manifest signature helpers.
 *
 * The expected SHA-256 of the server certificate's DER encoding is hard-coded
 * per release so an attacker with a rogue CA cannot MITM us. Update manifests
 * are signed with ed25519; the public key is embedded below and also shipped
 * as data/config/update-pubkey.pem for out-of-band rotation.
 */

import { createHash, createPublicKey, KeyObject, verify } from "crypto";
import { readFileSync } from "fs";

export interface PinRegistry {
  [host: string]: string; // lowercase hex sha256, 64 chars
}

const registry: PinRegistry = {};

/**
 * Embedded ed25519 public key (PEM). Keep in sync with
 * data/config/update-pubkey.pem. The private counterpart lives in keys/
 * (gitignored) during development and in a secrets manager in production.
 */
export const UPDATE_PUBKEY_PEM = `-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA5EJlOZ5e2iCenRHivFdYsxc0lUunKdiHn7NkvkjdpZM=
-----END PUBLIC KEY-----
`;

export function pinCertificate(host: string, sha256Hex: string): void {
  if (!/^[0-9a-f]{64}$/i.test(sha256Hex)) {
    throw new Error(`invalid sha256 pin for ${host}`);
  }
  registry[host.toLowerCase()] = sha256Hex.toLowerCase();
}

export function getPin(host: string): string | undefined {
  return registry[host.toLowerCase()];
}

/** Verify a DER-encoded certificate against the registered pin. */
export function verifyCertificate(host: string, derBytes: Buffer): boolean {
  const pin = getPin(host);
  if (!pin) {
    return false;
  }
  const digest = createHash("sha256").update(derBytes).digest("hex");
  return timingSafeEqualHex(digest, pin);
}

export function checkIntegrity(path: string, expectedSha256: string): boolean {
  const data = readFileSync(path);
  const digest = createHash("sha256").update(data).digest("hex");
  return timingSafeEqualHex(digest, expectedSha256.toLowerCase());
}

/**
 * Verify an ed25519 signature over an update manifest.
 *
 * @param manifest       Raw manifest bytes (Buffer).
 * @param signatureHex   Signature as lowercase hex (64 bytes / 128 chars).
 * @param pubkeyPem      Optional override PEM; defaults to the embedded key.
 * @returns              true iff the signature is valid for this manifest
 *                       and the provided/embedded public key.
 */
export function verifyUpdateSignature(
  manifest: Buffer,
  signatureHex: string,
  pubkeyPem?: string,
): boolean {
  if (!manifest || manifest.length === 0) return false;
  if (!/^[0-9a-f]+$/i.test(signatureHex)) return false;
  if (signatureHex.length % 2 !== 0) return false;

  const sig = Buffer.from(signatureHex, "hex");
  // ed25519 signatures are always 64 bytes.
  if (sig.length !== 64) return false;

  let key: KeyObject;
  try {
    key = createPublicKey(pubkeyPem && pubkeyPem.length > 0 ? pubkeyPem : UPDATE_PUBKEY_PEM);
  } catch {
    return false;
  }

  try {
    // ed25519 uses the single-shot `verify(null, ...)` path.
    return verify(null, manifest, key, sig);
  } catch {
    return false;
  }
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}
