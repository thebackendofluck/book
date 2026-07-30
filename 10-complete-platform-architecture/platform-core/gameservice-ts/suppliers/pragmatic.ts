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
 * suppliers/pragmatic.ts
 * -----------------------
 * Pragmatic Play — Slots + Live Casino (MD5 auth, edge-compatible).
 *
 * MD5 note: Web Crypto API does not support MD5 natively. We use a
 * pure-JS MD5 implementation to stay dependency-free. The md5() function
 * below is a minimal implementation for signing only — not for security.
 */

import type { AccountsProvider, PlayerSession, SupplierOperation, TransactionContext } from "../accounts-provider";
import { DebitOperation, CreditOperation } from "../accounts-provider";
import { BalanceStatus, TransactionResult, TransactionType, successResult } from "../transaction-result";
import { AuthenticationError } from "../transaction-result";

// ---------------------------------------------------------------------------
// Minimal MD5 (pure JS — no Node.js, no npm dep)
// Required because Pragmatic uses MD5 for request signing (legacy protocol)
// ---------------------------------------------------------------------------

function md5(input: string): string {
  /**
   * RFC 1321-compliant MD5 implementation.
   * Source: adapted from public-domain JS implementations.
   * This is adequate for HMAC-style signing, not for security primitives.
   */
  function safeAdd(x: number, y: number): number {
    const lsw = (x & 0xffff) + (y & 0xffff);
    const msw = (x >> 16) + (y >> 16) + (lsw >> 16);
    return (msw << 16) | (lsw & 0xffff);
  }
  function bitRotateLeft(num: number, cnt: number): number {
    return (num << cnt) | (num >>> (32 - cnt));
  }
  function md5cmn(q: number, a: number, b: number, x: number, s: number, t: number): number {
    return safeAdd(bitRotateLeft(safeAdd(safeAdd(a, q), safeAdd(x, t)), s), b);
  }
  function md5ff(a: number, b: number, c: number, d: number, x: number, s: number, t: number): number {
    return md5cmn((b & c) | (~b & d), a, b, x, s, t);
  }
  function md5gg(a: number, b: number, c: number, d: number, x: number, s: number, t: number): number {
    return md5cmn((b & d) | (c & ~d), a, b, x, s, t);
  }
  function md5hh(a: number, b: number, c: number, d: number, x: number, s: number, t: number): number {
    return md5cmn(b ^ c ^ d, a, b, x, s, t);
  }
  function md5ii(a: number, b: number, c: number, d: number, x: number, s: number, t: number): number {
    return md5cmn(c ^ (b | ~d), a, b, x, s, t);
  }

  // Typed index getter to satisfy noUncheckedIndexedAccess without per-call non-null assertions
  const at = (arr: Uint32Array | Uint8Array, idx: number): number => (arr[idx] ?? 0);
  const str8 = new TextEncoder().encode(input);
  const length8 = str8.length;
  const nblks = ((length8 + 8) >> 6) + 1;
  const blks = new Uint32Array(nblks * 16);
  for (let i = 0; i < length8; i++) {
    blks[i >> 2] = (at(blks, i >> 2)) | (at(str8, i) << ((i % 4) * 8));
  }
  blks[length8 >> 2] = (at(blks, length8 >> 2)) | (0x80 << ((length8 % 4) * 8));
  blks[nblks * 16 - 2] = length8 * 8;

  let a = 1732584193, b = -271733879, c = -1732584194, d = 271733878;

  for (let i = 0; i < blks.length; i += 16) {
    const olda = a, oldb = b, oldc = c, oldd = d;
    a = md5ff(a, b, c, d, at(blks, i + 0), 7, -680876936);
    d = md5ff(d, a, b, c, at(blks, i + 1), 12, -389564586);
    c = md5ff(c, d, a, b, at(blks, i + 2), 17, 606105819);
    b = md5ff(b, c, d, a, at(blks, i + 3), 22, -1044525330);
    a = md5ff(a, b, c, d, at(blks, i + 4), 7, -176418897);
    d = md5ff(d, a, b, c, at(blks, i + 5), 12, 1200080426);
    c = md5ff(c, d, a, b, at(blks, i + 6), 17, -1473231341);
    b = md5ff(b, c, d, a, at(blks, i + 7), 22, -45705983);
    a = md5ff(a, b, c, d, at(blks, i + 8), 7, 1770035416);
    d = md5ff(d, a, b, c, at(blks, i + 9), 12, -1958414417);
    c = md5ff(c, d, a, b, at(blks, i + 10), 17, -42063);
    b = md5ff(b, c, d, a, at(blks, i + 11), 22, -1990404162);
    a = md5ff(a, b, c, d, at(blks, i + 12), 7, 1804603682);
    d = md5ff(d, a, b, c, at(blks, i + 13), 12, -40341101);
    c = md5ff(c, d, a, b, at(blks, i + 14), 17, -1502002290);
    b = md5ff(b, c, d, a, at(blks, i + 15), 22, 1236535329);
    a = md5gg(a, b, c, d, at(blks, i + 1), 5, -165796510);
    d = md5gg(d, a, b, c, at(blks, i + 6), 9, -1069501632);
    c = md5gg(c, d, a, b, at(blks, i + 11), 14, 643717713);
    b = md5gg(b, c, d, a, at(blks, i + 0), 20, -373897302);
    a = md5gg(a, b, c, d, at(blks, i + 5), 5, -701558691);
    d = md5gg(d, a, b, c, at(blks, i + 10), 9, 38016083);
    c = md5gg(c, d, a, b, at(blks, i + 15), 14, -660478335);
    b = md5gg(b, c, d, a, at(blks, i + 4), 20, -405537848);
    a = md5gg(a, b, c, d, at(blks, i + 9), 5, 568446438);
    d = md5gg(d, a, b, c, at(blks, i + 14), 9, -1019803690);
    c = md5gg(c, d, a, b, at(blks, i + 3), 14, -187363961);
    b = md5gg(b, c, d, a, at(blks, i + 8), 20, 1163531501);
    a = md5gg(a, b, c, d, at(blks, i + 13), 5, -1444681467);
    d = md5gg(d, a, b, c, at(blks, i + 2), 9, -51403784);
    c = md5gg(c, d, a, b, at(blks, i + 7), 14, 1735328473);
    b = md5gg(b, c, d, a, at(blks, i + 12), 20, -1926607734);
    a = md5hh(a, b, c, d, at(blks, i + 5), 4, -378558);
    d = md5hh(d, a, b, c, at(blks, i + 8), 11, -2022574463);
    c = md5hh(c, d, a, b, at(blks, i + 11), 16, 1839030562);
    b = md5hh(b, c, d, a, at(blks, i + 14), 23, -35309556);
    a = md5hh(a, b, c, d, at(blks, i + 1), 4, -1530992060);
    d = md5hh(d, a, b, c, at(blks, i + 4), 11, 1272893353);
    c = md5hh(c, d, a, b, at(blks, i + 7), 16, -155497632);
    b = md5hh(b, c, d, a, at(blks, i + 10), 23, -1094730640);
    a = md5hh(a, b, c, d, at(blks, i + 13), 4, 681279174);
    d = md5hh(d, a, b, c, at(blks, i + 0), 11, -358537222);
    c = md5hh(c, d, a, b, at(blks, i + 3), 16, -722521979);
    b = md5hh(b, c, d, a, at(blks, i + 6), 23, 76029189);
    a = md5hh(a, b, c, d, at(blks, i + 9), 4, -640364487);
    d = md5hh(d, a, b, c, at(blks, i + 12), 11, -421815835);
    c = md5hh(c, d, a, b, at(blks, i + 15), 16, 530742520);
    b = md5hh(b, c, d, a, at(blks, i + 2), 23, -995338651);
    a = md5ii(a, b, c, d, at(blks, i + 0), 6, -198630844);
    d = md5ii(d, a, b, c, at(blks, i + 7), 10, 1126891415);
    c = md5ii(c, d, a, b, at(blks, i + 14), 15, -1416354905);
    b = md5ii(b, c, d, a, at(blks, i + 5), 21, -57434055);
    a = md5ii(a, b, c, d, at(blks, i + 12), 6, 1700485571);
    d = md5ii(d, a, b, c, at(blks, i + 3), 10, -1894986606);
    c = md5ii(c, d, a, b, at(blks, i + 10), 15, -1051523);
    b = md5ii(b, c, d, a, at(blks, i + 1), 21, -2054922799);
    a = md5ii(a, b, c, d, at(blks, i + 8), 6, 1873313359);
    d = md5ii(d, a, b, c, at(blks, i + 15), 10, -30611744);
    c = md5ii(c, d, a, b, at(blks, i + 6), 15, -1560198380);
    b = md5ii(b, c, d, a, at(blks, i + 13), 21, 1309151649);
    a = md5ii(a, b, c, d, at(blks, i + 4), 6, -145523070);
    d = md5ii(d, a, b, c, at(blks, i + 11), 10, -1120210379);
    c = md5ii(c, d, a, b, at(blks, i + 2), 15, 718787259);
    b = md5ii(b, c, d, a, at(blks, i + 9), 21, -343485551);
    a = safeAdd(a, olda);
    b = safeAdd(b, oldb);
    c = safeAdd(c, oldc);
    d = safeAdd(d, oldd);
  }

  const result = [a, b, c, d].map((n) => {
    let hex = "";
    for (let j = 0; j < 4; j++) {
      hex += ((n >> (j * 8)) & 0xff).toString(16).padStart(2, "0");
    }
    return hex;
  }).join("");
  return result.toUpperCase();
}

// ---------------------------------------------------------------------------
// Hash builder
// ---------------------------------------------------------------------------

export function buildPragmaticHash(params: Record<string, string>, secretKey: string): string {
  const sorted = Object.entries(params)
    .filter(([k]) => k !== "hash")
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}${v}`)
    .join("");
  return md5(sorted + secretKey);
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class PragmaticProvider implements AccountsProvider {
  constructor(
    private readonly secretKey: string,
    private readonly operatorId: string,
  ) {}

  verifyHash(params: Record<string, string>): boolean {
    const received = (params.hash ?? "").toUpperCase();
    const expected = buildPragmaticHash(params, this.secretKey);
    return received === expected;
  }

  async authenticate(token: string): Promise<PlayerSession> {
    throw new AuthenticationError("Not implemented: validate Pragmatic session token");
  }

  async getBalance(session: PlayerSession): Promise<BalanceStatus> {
    return { cashBalance: "0", bonusBalance: "0", currency: session.currency || "EUR" };
  }

  async debit(s: PlayerSession, op: DebitOperation, ctx: TransactionContext): Promise<TransactionResult> {
    return this.applyTransaction(s, [op], ctx);
  }

  async credit(s: PlayerSession, op: CreditOperation, ctx: TransactionContext): Promise<TransactionResult> {
    return this.applyTransaction(s, [op], ctx);
  }

  async refund(s: PlayerSession, op: any, ctx: TransactionContext): Promise<TransactionResult> {
    return this.reverseTransaction(s, [op], ctx);
  }

  async applyTransaction(session: PlayerSession, operations: SupplierOperation[], context: TransactionContext): Promise<TransactionResult> {
    const balance: BalanceStatus = { cashBalance: "0", bonusBalance: "0", currency: session.currency || "EUR" };
    return successResult(TransactionType.DEBIT, balance, { txId: context.txId, externalId: context.supplierRef });
  }

  async reverseTransaction(session: PlayerSession, operations: SupplierOperation[], context: TransactionContext): Promise<TransactionResult> {
    const balance: BalanceStatus = { cashBalance: "0", bonusBalance: "0", currency: session.currency || "EUR" };
    return successResult(TransactionType.REFUND, balance, { txId: context.txId, externalId: context.supplierRef });
  }
}
