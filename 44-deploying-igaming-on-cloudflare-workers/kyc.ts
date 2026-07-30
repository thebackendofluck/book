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
 * AcmeToCasino Platform - KYC Verification
 * Document verification, identity checks, KYC status management
 */

import {
  Env,
  successResponse,
  errorResponse,
  internalErrorResponse,
  parseJSON,
} from './utils.js';
import { authenticateRequest, UserRow } from './auth.js';
// Remote HSM: hybrid edge+HSM encryption for KYC PII fields.
// Encrypt with local AES-256-GCM (<1 ms) + sign with YubiHSM (~50 ms).
// See remote-hsm/REMOTE-HSM-GUIDE.md for architecture and compliance analysis.
import { HybridCipher, serializeField, deserializeField } from './remote-hsm/hybrid-encryption.js';

// ─── Types ─────────────────────────────────────────────────────────────────

type KycLevel = 'none' | 'basic' | 'standard' | 'enhanced';
type KycStatus = 'not_started' | 'pending' | 'approved' | 'rejected' | 'expired';
type DocumentType = 'passport' | 'national_id' | 'drivers_license' | 'proof_of_address' | 'source_of_funds';

interface KycRecord {
  id: number;
  user_id: number;
  level: KycLevel;
  status: KycStatus;
  document_type: DocumentType | null;
  document_ref: string | null;  // R2 object key
  reviewer_notes: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  expires_at: string | null;
  created_at: string;
}

interface SubmitKycBody {
  level: KycLevel;
  documentType: DocumentType;
  documentData: string; // base64-encoded document image/PDF
  documentMimeType: string;
}

// ─── Route handler ─────────────────────────────────────────────────────────

export async function handleKyc(request: Request, env: Env): Promise<Response> {
  const user = await authenticateRequest(request, env);
  if (!user) return errorResponse('Unauthorized', 401);

  const url = new URL(request.url);
  const { method } = request;

  if (method === 'GET' && url.pathname === '/api/kyc/status') {
    return handleKycStatus(user, env);
  }
  if (method === 'POST' && url.pathname === '/api/kyc/submit') {
    return handleKycSubmit(request, env, user);
  }
  if (method === 'GET' && url.pathname === '/api/kyc/requirements') {
    return handleKycRequirements(user, env);
  }

  return errorResponse('Route not found', 404);
}

// ─── KYC Status ────────────────────────────────────────────────────────────

async function handleKycStatus(user: UserRow, env: Env): Promise<Response> {
  try {
    const records = await env.DB.prepare(
      'SELECT * FROM kyc_records WHERE user_id = ? ORDER BY created_at DESC'
    )
      .bind(user.id)
      .all<KycRecord>();

    const latestByLevel = new Map<KycLevel, KycRecord>();
    for (const record of records.results) {
      if (!latestByLevel.has(record.level)) {
        latestByLevel.set(record.level, record);
      }
    }

    const currentLevel = deriveCurrentLevel(latestByLevel);
    const pendingSubmissions = records.results.filter((r) => r.status === 'pending');

    return successResponse({
      currentLevel,
      records: Array.from(latestByLevel.values()).map(sanitizeRecord),
      hasPendingSubmission: pendingSubmissions.length > 0,
      depositLimitWithoutKyc: 2000,   // EUR equivalent
      withdrawalRequiresKyc: true,
    });
  } catch (err) {
    console.error('KYC status error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── KYC Submit ────────────────────────────────────────────────────────────

async function handleKycSubmit(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<SubmitKycBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  const errors = validateKycSubmission(body);
  if (errors.length > 0) return errorResponse(errors.join(', '), 422);

  // Check for existing pending submission
  const existing = await env.DB.prepare(
    "SELECT id FROM kyc_records WHERE user_id = ? AND level = ? AND status = 'pending'"
  )
    .bind(user.id, body.level)
    .first<{ id: number }>();

  if (existing) {
    return errorResponse('You already have a pending submission for this KYC level', 409);
  }

  try {
    // Store document in R2
    const documentKey = await storeDocument(
      body.documentData,
      body.documentMimeType,
      user.id,
      body.documentType,
      env
    );

    // Create KYC record
    const result = await env.DB.prepare(
      `INSERT INTO kyc_records
         (user_id, level, status, document_type, document_ref, submitted_at)
       VALUES (?, ?, 'pending', ?, ?, CURRENT_TIMESTAMP)`
    )
      .bind(user.id, body.level, body.documentType, documentKey)
      .run();

    const recordId = result.meta.last_row_id as number;

    // Log compliance event
    await env.DB.prepare(
      `INSERT INTO compliance_events (user_id, event_type, details)
       VALUES (?, 'kyc_submission', ?)`
    )
      .bind(user.id, JSON.stringify({ recordId, level: body.level, documentType: body.documentType }))
      .run();

    return successResponse({
      recordId,
      status: 'pending',
      message: 'Your documents have been submitted for review. This typically takes 1-2 business days.',
    }, 201);
  } catch (err) {
    console.error('KYC submit error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── KYC Requirements ─────────────────────────────────────────────────────

async function handleKycRequirements(user: UserRow, env: Env): Promise<Response> {
  try {
    const records = await env.DB.prepare(
      "SELECT level, status FROM kyc_records WHERE user_id = ? AND status = 'approved'"
    )
      .bind(user.id)
      .all<{ level: KycLevel; status: string }>();

    const approvedLevels = new Set(records.results.map((r) => r.level));

    const requirements = [
      {
        level: 'basic' as KycLevel,
        completed: approvedLevels.has('basic'),
        requiredDocuments: ['national_id', 'passport', 'drivers_license'],
        description: 'Identity verification — required to withdraw funds',
        depositLimit: 2000,
        withdrawalLimit: 1000,
      },
      {
        level: 'standard' as KycLevel,
        completed: approvedLevels.has('standard'),
        requiredDocuments: ['proof_of_address'],
        description: 'Address verification — required for withdrawals over €1,000',
        depositLimit: 10000,
        withdrawalLimit: 5000,
      },
      {
        level: 'enhanced' as KycLevel,
        completed: approvedLevels.has('enhanced'),
        requiredDocuments: ['source_of_funds'],
        description: 'Source of funds verification — required for high-value players',
        depositLimit: null,
        withdrawalLimit: null,
      },
    ];

    return successResponse({ requirements });
  } catch (err) {
    console.error('KYC requirements error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Helpers ───────────────────────────────────────────────────────────────

async function storeDocument(
  base64Data: string,
  mimeType: string,
  userId: number,
  docType: DocumentType,
  env: Env
): Promise<string> {
  const key = `kyc/${userId}/${docType}/${Date.now()}`;
  const binaryString = atob(base64Data);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  await env.STORAGE.put(key, bytes, {
    httpMetadata: { contentType: mimeType },
    customMetadata: { userId: String(userId), documentType: docType },
  });

  return key;
}

function deriveCurrentLevel(records: Map<KycLevel, KycRecord>): KycLevel {
  const levels: KycLevel[] = ['enhanced', 'standard', 'basic', 'none'];
  for (const level of levels) {
    const record = records.get(level);
    if (record?.status === 'approved') return level;
  }
  return 'none';
}

function sanitizeRecord(record: KycRecord): Omit<KycRecord, 'document_ref'> {
  const { document_ref: _ref, ...safe } = record;
  return safe;
}

function validateKycSubmission(body: SubmitKycBody): string[] {
  const errors: string[] = [];
  const validLevels: KycLevel[] = ['basic', 'standard', 'enhanced'];
  const validDocs: DocumentType[] = [
    'passport', 'national_id', 'drivers_license', 'proof_of_address', 'source_of_funds',
  ];

  if (!validLevels.includes(body.level)) {
    errors.push(`level must be one of: ${validLevels.join(', ')}`);
  }
  if (!validDocs.includes(body.documentType)) {
    errors.push(`documentType must be one of: ${validDocs.join(', ')}`);
  }
  if (!body.documentData || typeof body.documentData !== 'string') {
    errors.push('documentData is required (base64-encoded)');
  }
  if (!body.documentMimeType || !['image/jpeg', 'image/png', 'application/pdf'].includes(body.documentMimeType)) {
    errors.push('documentMimeType must be image/jpeg, image/png, or application/pdf');
  }
  // Rough size check: base64 string should not exceed ~10 MB
  if (body.documentData && body.documentData.length > 13_500_000) {
    errors.push('Document size exceeds 10MB limit');
  }
  return errors;
}

// ─── Remote HSM helpers (Chapter 44 hybrid pattern) ───────────────────────
//
// These helpers demonstrate the hybrid edge+HSM encryption pattern for KYC
// PII fields. The approach:
//   1. Encrypt with local AES-256-GCM (Web Crypto, <1 ms)
//   2. Send SHA-256(ciphertext) to HSM for ECDSA signature (~50 ms)
//   3. Store ciphertext + signature in D1
//
// The signature provides non-repudiation: it proves the ciphertext was
// produced by an authenticated Worker, not injected directly into D1.
// Required for PCI DSS Req 3.5.1 and LGPD Art.46 compliance.
//
// Note: HSM_API_URL and HSM_API_KEY must be set as Workers Secrets.
// If either is absent (development), the function falls back to plain
// FieldCipher encryption (no HSM signature).

/**
 * Encrypt a KYC PII field with hybrid edge+HSM encryption.
 * Falls back to plain Web Crypto if HSM secrets are not configured.
 *
 * @param plaintext  The raw PII value (e.g., CPF, passport number)
 * @param env        Worker environment (must include ENCRYPTION_KEY, HSM_API_*)
 * @returns JSON string suitable for D1 TEXT column storage
 */
export async function encryptKycField(plaintext: string, env: Env): Promise<string> {
  if (!env.HSM_API_URL || !env.HSM_API_KEY || env.HSM_API_URL === '') {
    // Development fallback — plain AES-256-GCM, no HSM signature
    const { FieldCipher } = await import('./gdpr-encryption/field-cipher.js');
    const cipher = await FieldCipher.fromSecret(env.ENCRYPTION_KEY);
    return JSON.stringify(await cipher.encrypt(plaintext));
  }

  const cipher = await HybridCipher.create(
    env.ENCRYPTION_KEY,
    env.HSM_API_URL,
    env.HSM_API_KEY,
    { allowUnsigned: false },  // Strict: HSM failure = write failure
  );
  return serializeField(await cipher.encrypt(plaintext));
}

/**
 * Decrypt a KYC PII field, verifying the HSM signature if present.
 *
 * @param raw  JSON string from D1 (output of encryptKycField)
 * @param env  Worker environment
 * @returns The original plaintext string
 */
export async function decryptKycField(raw: string, env: Env): Promise<string> {
  const parsed = JSON.parse(raw) as { sig?: string };

  if (!parsed.sig) {
    // Legacy record encrypted with plain FieldCipher (no signature)
    const { FieldCipher } = await import('./gdpr-encryption/field-cipher.js');
    const cipher = await FieldCipher.fromSecret(env.ENCRYPTION_KEY);
    return cipher.decrypt(parsed as Parameters<typeof cipher.decrypt>[0]);
  }

  if (!env.HSM_API_URL || !env.HSM_API_KEY) {
    throw new Error('HSM_API_URL / HSM_API_KEY required to decrypt HSM-signed fields');
  }

  const cipher = await HybridCipher.create(
    env.ENCRYPTION_KEY,
    env.HSM_API_URL,
    env.HSM_API_KEY,
  );
  return cipher.decrypt(deserializeField(raw), true);
}
