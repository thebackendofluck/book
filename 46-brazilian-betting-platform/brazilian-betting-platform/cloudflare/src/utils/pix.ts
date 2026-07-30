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
 * PIX Utilities
 *
 * QR code payload generation (EMV® Merchant-Presented QR Code, BACEN spec),
 * HMAC-SHA256 webhook signature validation, and transaction ID helpers.
 *
 * NOTE: generatePixPayload emits a self-contained (static-style) BR Code
 * with the amount and txid inlined. A true dynamic (cob) QR instead carries
 * a location URL in the merchant-account field and *** as the reference
 * label, with amount/txid living in the cob resource. This module is the
 * simplified static form.
 *
 * Reference specs:
 *  - BACEN Manual de Padrões para Iniciação do PIX (2021)
 *  - EMV® QRCPS-MPM (Merchant Presentation Mode) specification
 *
 * Note: actual QR image rendering requires a client-side library. This module
 * produces the PIX "Copia e Cola" string that encodes to a QR image.
 */

/**
 * Operator risk-policy deposit bounds (NOT statutory: Lei 14.790/SIGAP
 * prescribe no universal fixed thresholds). Tune per licence.
 */
export const PIX_MIN_BRL = 10;
export const PIX_MAX_BRL = 50_000;

// ── Payload IDs (EMV tag numbers) ────────────────────────────────────────────

const TAG_PAYLOAD_FORMAT      = '00';
const TAG_POINT_OF_INITIATION = '01';
const TAG_MERCHANT_ACCOUNT    = '26';
const TAG_MERCHANT_CATEGORY   = '52';
const TAG_TRANSACTION_CURRENCY = '53';
const TAG_TRANSACTION_AMOUNT  = '54';
const TAG_COUNTRY_CODE        = '58';
const TAG_MERCHANT_NAME       = '59';
const TAG_MERCHANT_CITY       = '60';
const TAG_ADDITIONAL_DATA     = '62';
const TAG_CRC                 = '63';

const GUI_PIX = '0014BR.GOV.BCB.PIX';

/**
 * Generate a PIX EMV Merchant-Presented QR Code payload string ("Copia e Cola").
 *
 * @param pixKey        - Operator PIX key (CNPJ, e-mail, phone, or random key).
 * @param amountBRL     - Transaction amount in BRL (e.g. 100.50).
 * @param merchantName  - Trading name (max 25 chars, no special chars).
 * @param merchantCity  - City (max 15 chars).
 * @param txid          - Reference label for the static EMV field 62-05
 *                        (max 25 chars). NOTE: this is NOT the cob/cobv API
 *                        txid, which must be 26-35 alphanumeric chars.
 * @returns             PIX Copia e Cola string.
 */
export function generatePixPayload(
  pixKey: string,
  amountBRL: number,
  merchantName: string,
  merchantCity: string,
  txid: string
): string {
  if (amountBRL < PIX_MIN_BRL || amountBRL > PIX_MAX_BRL) {
    throw new RangeError(
      `PIX amount must be between R$ ${PIX_MIN_BRL} and R$ ${PIX_MAX_BRL}`
    );
  }

  const cleanMerchantName = sanitizeEmvField(merchantName, 25);
  const cleanMerchantCity = sanitizeEmvField(merchantCity, 15);
  // 25-char cap is correct ONLY for the static EMV 62-05 reference label.
  const cleanTxid         = sanitizeAlphanumeric(txid, 25);
  const amountStr         = amountBRL.toFixed(2);

  // Merchant account (tag 26): GUI + PIX key
  const pixKeyField     = tlv('01', pixKey);
  const merchantAccount = tlv(TAG_MERCHANT_ACCOUNT, GUI_PIX + pixKeyField);

  // Additional data field (tag 62): TXID sub-field (tag 05)
  const txidField      = tlv('05', cleanTxid);
  const additionalData = tlv(TAG_ADDITIONAL_DATA, txidField);

  // Build payload without CRC
  const withoutCrc =
    tlv(TAG_PAYLOAD_FORMAT, '01') +          // payload format indicator
    tlv(TAG_POINT_OF_INITIATION, '12') +     // dynamic QR (not reusable)
    merchantAccount +
    tlv(TAG_MERCHANT_CATEGORY, '0000') +     // MCC: not used for PIX
    tlv(TAG_TRANSACTION_CURRENCY, '986') +   // BRL ISO 4217
    tlv(TAG_TRANSACTION_AMOUNT, amountStr) +
    tlv(TAG_COUNTRY_CODE, 'BR') +
    tlv(TAG_MERCHANT_NAME, cleanMerchantName) +
    tlv(TAG_MERCHANT_CITY, cleanMerchantCity) +
    additionalData;

  const crc = crc16CCITT(withoutCrc);
  return withoutCrc + TAG_CRC + crc.toString(16).toUpperCase().padStart(4, '0');
}

/**
 * Validate an inbound PIX webhook HMAC-SHA256 signature.
 *
 * The PSP signs the raw request body with the shared secret and sends the
 * signature as a hex string in the `x-pix-hmac` header.
 *
 * @param body      - Raw request body as ArrayBuffer.
 * @param signature - Hex-encoded HMAC-SHA256 signature from PSP.
 * @param secret    - Shared HMAC secret (from `PIX_HMAC_SECRET` env var).
 * @returns true if the signature matches.
 */
export async function validatePixHmac(
  body: ArrayBuffer,
  signature: string,
  secret: string
): Promise<boolean> {
  // Fail closed: an empty or unset secret must never verify a signature.
  if (!secret) return false;
  try {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify']
    );

    const sigBytes = hexToBytes(signature);
    return await crypto.subtle.verify('HMAC', key, sigBytes, body);
  } catch {
    return false;
  }
}

/**
 * Generate a unique PIX transaction ID (txid) for the cob/cobv API.
 * A dynamic-charge txid must match ^[a-zA-Z0-9]{26,35}$; here `BET` + 29
 * random alphanumeric chars = 32 chars, within that range.
 */
export function generatePixTxid(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  const charsLen = chars.length; // 62
  // Use rejection sampling to eliminate modulo bias.
  // Accept bytes only in the largest multiple of charsLen that fits in 0-255.
  const limit = 256 - (256 % charsLen); // 256 - (256 % 62) = 256 - 8 = 248
  const result: string[] = [];
  while (result.length < 29) {
    const buf = crypto.getRandomValues(new Uint8Array(29 - result.length + 8));
    for (const b of buf) {
      if (b < limit) {
        result.push(chars[b % charsLen]);
        if (result.length === 29) break;
      }
    }
  }
  return `BET${result.join('')}`;
}

/**
 * Parse the BRL amount string from a PIX webhook payload into integer centavos.
 * BACEN sends amounts as decimal strings like "50.00".
 *
 * @throws {RangeError} if value cannot be parsed.
 */
export function pixValueToCentavos(valor: string): number {
  const parsed = parseFloat(valor);
  if (!isFinite(parsed) || parsed <= 0) {
    throw new RangeError(`Invalid PIX valor: "${valor}"`);
  }
  return Math.round(parsed * 100);
}

// ── Internal helpers ─────────────────────────────────────────────────────────

/** Build an EMV TLV (Tag-Length-Value) triplet. */
function tlv(tag: string, value: string): string {
  const len = value.length.toString().padStart(2, '0');
  return `${tag}${len}${value}`;
}

/** Remove characters not allowed in EMV merchant name / city fields. */
function sanitizeEmvField(value: string, maxLen: number): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '') // strip diacritics
    .replace(/[^A-Za-z0-9 ]/g, '')
    .toUpperCase()
    .slice(0, maxLen);
}

/** Keep only alphanumeric characters for TXID. */
function sanitizeAlphanumeric(value: string, maxLen: number): string {
  return value.replace(/[^A-Za-z0-9]/g, '').slice(0, maxLen);
}

/** CRC-16/CCITT-FALSE as required by EMV QR Code spec. */
function crc16CCITT(data: string): number {
  let crc = 0xffff;
  for (let i = 0; i < data.length; i++) {
    crc ^= data.charCodeAt(i) << 8;
    for (let j = 0; j < 8; j++) {
      crc = crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1;
    }
    crc &= 0xffff;
  }
  return crc;
}

/** Convert a hex string to a Uint8Array. */
function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0) throw new Error('Odd-length hex string');
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}
