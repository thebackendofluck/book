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
 * Cloudflare Worker client for remote HSM operations.
 *
 * Communicates with the HSM Proxy API running on ops-host
 * (behind nginx mTLS at https://hsm-api.acmetocasino.com:8443).
 *
 * Authentication uses defense-in-depth:
 *   1. mTLS client certificate (bound via Workers mTLS cert binding)
 *   2. X-API-Key header (second layer, checked by FastAPI upstream)
 *
 * mTLS setup:
 *   - Client cert issued by OpenBao pki-mtls (CA: "AcmeToCasino mTLS CA")
 *   - Cert CN must be "cloudflare-worker-hsm-client"
 *   - Upload cert to Cloudflare:
 *       curl -X POST https://api.cloudflare.com/client/v4/accounts/ACCT_ID/mtls_certificates \
 *           -H "Authorization: Bearer $CF_API_TOKEN" \
 *           -F "certificate=@worker-client.crt" \
 *           -F "private_key=@worker-client.key" \
 *           -F "name=hsm-proxy-client"
 *   - Add to wrangler.toml:
 *       [[mtls_certificates]]
 *       binding = "HSM_CLIENT_CERT"
 *       certificate_id = "<id-from-upload>"
 *
 * Set secrets before deploying:
 *   npx wrangler secret put HSM_API_URL    # https://hsm-api.acmetocasino.com:8443
 *   npx wrangler secret put HSM_API_KEY    # 32-byte hex from /etc/hsm-proxy-api/env
 *
 * Usage — mTLS encrypt (with cert binding):
 *   const hsm = new RemoteHSM(env.HSM_API_URL, env.HSM_API_KEY, 15_000, env.HSM_CLIENT_CERT);
 *   const ciphertext = await hsm.encrypt('player@example.com');
 *
 * Usage — API-key only (legacy, no mTLS):
 *   const hsm = new RemoteHSM(env.HSM_API_URL, env.HSM_API_KEY);
 *
 * Latency budget (measured ops-host loopback — real network adds RTT):
 *   Without mTLS (HTTP direct): p50=14ms p95=16ms p99=28ms
 *   With mTLS    (HTTPS+cert):  p50=21ms p95=25ms p99=26ms
 *   mTLS overhead: ~7ms p50 (amortised with connection reuse in production)
 *
 * PCI DSS 4.0.1 Req 4.2.1 compliance:
 *   mTLS satisfies "strong cryptography for all transmissions of account data"
 *   — both the transport (TLS 1.3) and the authentication (mutual cert) are
 *   cryptographically strong per NIST SP 800-52r2.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

export interface HSMEncryptResult {
  ciphertext: string;    // "vault:v1:…" — pass to hsm.decrypt() to reverse
  key_name: string;
}

export interface HSMDecryptResult {
  plaintext: string;     // base64-encoded original plaintext
  key_name: string;
}

export interface HSMSignResult {
  signature: string;     // "vault:v1:…" — pass to hsm.verify() to confirm
  key_name: string;
}

export interface HSMVerifyResult {
  valid: boolean;
  key_name: string;
}

export interface HSMRandomResult {
  random_bytes: string;  // base64-encoded random bytes
  count: number;
  encoding: 'base64';
}

export interface HSMHealth {
  api: string;
  openbao: string;
  yubihsm_connector: string;
  timestamp: number;
}

// ─── Error class ─────────────────────────────────────────────────────────────

export class HSMError extends Error {
  constructor(
    message: string,
    public readonly statusCode: number,
    public readonly operation: string,
  ) {
    super(message);
    this.name = 'HSMError';
  }
}

// ─── Client ──────────────────────────────────────────────────────────────────

/** Cloudflare Workers mTLS certificate fetcher binding type. */
export interface MTLSCertBinding {
  fetch(url: string | Request, init?: RequestInit): Promise<Response>;
}

export class RemoteHSM {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;
  private readonly mtlsCert: MTLSCertBinding | null;

  /**
   * @param apiUrl    Full base URL of the HSM proxy API, no trailing slash.
   *                  Example: "https://hsm-api.acmetocasino.com:8443"
   * @param apiKey    32-byte hex API key matching HSM_API_KEY on the server.
   * @param timeoutMs Per-request timeout (default 15 s — HSM ops can be slow).
   * @param mtlsCert  Optional: Workers mTLS cert binding (env.HSM_CLIENT_CERT).
   *                  When provided, requests are made with the mTLS client cert.
   *                  When absent, falls back to API-key-only (no mTLS).
   */
  constructor(
    apiUrl: string,
    apiKey: string,
    timeoutMs = 15_000,
    mtlsCert?: MTLSCertBinding,
  ) {
    this.baseUrl = apiUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
    this.mtlsCert = mtlsCert ?? null;
  }

  // ─── Private helpers ───────────────────────────────────────────────────────

  private headers(): HeadersInit {
    return {
      'Content-Type': 'application/json',
      'X-API-Key': this.apiKey,
    };
  }

  private async post<T>(path: string, body: Record<string, unknown>): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    // Use mTLS cert binding if available, otherwise plain fetch
    const fetcher = this.mtlsCert ?? globalThis;
    let response: Response;
    try {
      response = await fetcher.fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timer);
      const msg = err instanceof Error ? err.message : String(err);
      throw new HSMError(`HSM request failed: ${msg}`, 0, path);
    }
    clearTimeout(timer);

    if (!response.ok) {
      let detail = response.statusText;
      try {
        const errBody = await response.json() as { detail?: string };
        detail = errBody.detail ?? detail;
      } catch {
        // ignore parse errors
      }
      throw new HSMError(
        `HSM API error on ${path}: ${detail}`,
        response.status,
        path,
      );
    }

    return response.json() as Promise<T>;
  }

  private async get<T>(path: string): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    const fetcher = this.mtlsCert ?? globalThis;
    let response: Response;
    try {
      response = await fetcher.fetch(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers: this.headers(),
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timer);
      const msg = err instanceof Error ? err.message : String(err);
      throw new HSMError(`HSM request failed: ${msg}`, 0, path);
    }
    clearTimeout(timer);

    if (!response.ok) {
      throw new HSMError(
        `HSM API error on ${path}: ${response.statusText}`,
        response.status,
        path,
      );
    }

    return response.json() as Promise<T>;
  }

  // ─── Public API ────────────────────────────────────────────────────────────

  /**
   * Encrypt a plaintext string using OpenBao Transit (YubiHSM-backed key).
   *
   * The plaintext is base64-encoded before sending (OpenBao Transit API
   * requirement). The returned ciphertext is an opaque versioned string
   * suitable for storage in D1.
   *
   * @param plaintext  Raw string to encrypt (e.g. email, CPF, phone number)
   * @param keyName    OpenBao Transit key name (default: "field-cipher")
   * @param context    Optional AAD context string (for derived keys)
   */
  async encrypt(
    plaintext: string,
    keyName = 'field-cipher',
    context?: string,
  ): Promise<HSMEncryptResult> {
    const plaintextB64 = btoa(unescape(encodeURIComponent(plaintext)));
    const body: Record<string, unknown> = {
      plaintext: plaintextB64,
      key_name: keyName,
    };
    if (context !== undefined) {
      body.context = btoa(context);
    }
    return this.post<HSMEncryptResult>('/hsm/encrypt', body);
  }

  /**
   * Decrypt a ciphertext string previously produced by encrypt().
   *
   * Returns the original plaintext string.
   */
  async decrypt(
    ciphertext: string,
    keyName = 'field-cipher',
    context?: string,
  ): Promise<string> {
    const body: Record<string, unknown> = { ciphertext, key_name: keyName };
    if (context !== undefined) {
      body.context = btoa(context);
    }
    const result = await this.post<HSMDecryptResult>('/hsm/decrypt', body);
    // OpenBao returns base64 plaintext — decode to UTF-8 string
    return decodeURIComponent(escape(atob(result.plaintext)));
  }

  /**
   * Sign data using an Ed25519 or ECDSA-P256 key in the YubiHSM.
   *
   * For the hybrid pattern, pre-hash the data locally to avoid sending
   * raw PII across the network:
   *   const hash = await crypto.subtle.digest('SHA-256', encoder.encode(data));
   *   const sig  = await hsm.signRaw(toBase64(hash), 'jwt-signing', true);
   *
   * @param data         Raw string to sign (encoded as base64 before sending)
   * @param keyName      OpenBao Transit key name (default: "jwt-signing")
   * @param hashAlgo     Hash algorithm: "sha2-256" | "sha2-512"
   */
  async sign(
    data: string,
    keyName = 'jwt-signing',
    hashAlgo = 'sha2-256',
  ): Promise<HSMSignResult> {
    const dataB64 = btoa(unescape(encodeURIComponent(data)));
    return this.post<HSMSignResult>('/hsm/sign', {
      data: dataB64,
      key_name: keyName,
      hash_algorithm: hashAlgo,
      prehashed: false,
    });
  }

  /**
   * Sign pre-hashed data (avoid sending raw plaintext to the HSM host).
   * Use this in the hybrid pattern when signing ciphertext hashes.
   *
   * @param hashB64   Base64-encoded pre-computed hash (SHA-256 or SHA-512)
   * @param keyName   OpenBao Transit key name
   * @param hashAlgo  Must match the algorithm used to produce the hash
   */
  async signHash(
    hashB64: string,
    keyName = 'jwt-signing',
    hashAlgo = 'sha2-256',
  ): Promise<HSMSignResult> {
    return this.post<HSMSignResult>('/hsm/sign', {
      data: hashB64,
      key_name: keyName,
      hash_algorithm: hashAlgo,
      prehashed: true,
    });
  }

  /**
   * Verify a signature previously produced by sign() or signHash().
   *
   * Throws HSMError if the signature is invalid (fail-loud).
   * Returns true on success.
   */
  async verify(
    data: string,
    signature: string,
    keyName = 'jwt-signing',
    hashAlgo = 'sha2-256',
  ): Promise<true> {
    const dataB64 = btoa(unescape(encodeURIComponent(data)));
    await this.post<HSMVerifyResult>('/hsm/verify', {
      data: dataB64,
      signature,
      key_name: keyName,
      hash_algorithm: hashAlgo,
      prehashed: false,
    });
    return true;
  }

  /**
   * Generate cryptographically random bytes from the YubiHSM TRNG.
   *
   * Use for session token generation, nonce creation, or any operation
   * requiring hardware-backed randomness. For most operations, Workers'
   * built-in crypto.getRandomValues() (FIPS 140-2 compliant via V8) is
   * sufficient and has zero latency.
   *
   * @param count  Number of bytes (1–1024)
   */
  async randomBytes(count = 32): Promise<Uint8Array> {
    const result = await this.post<HSMRandomResult>('/hsm/random', { count });
    const binary = atob(result.random_bytes);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  /**
   * Check HSM proxy liveness (unauthenticated on server, but we still send
   * the API key so we can detect auth failures early).
   */
  async health(): Promise<HSMHealth> {
    return this.get<HSMHealth>('/hsm/health');
  }
}

// ─── Utility helpers ─────────────────────────────────────────────────────────

/** Convert ArrayBuffer or Uint8Array to base64 string. */
export function toBase64(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

/** Convert base64 string back to Uint8Array. */
export function fromBase64(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** Convert Uint8Array to lowercase hex string. */
export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Compute SHA-256 of a string using Web Crypto, returning base64.
 * Use this to pre-hash data before calling hsm.signHash() in the
 * hybrid pattern — keeps raw PII off the network to the HSM host.
 */
export async function sha256Base64(data: string): Promise<string> {
  const encoder = new TextEncoder();
  const hash = await crypto.subtle.digest('SHA-256', encoder.encode(data));
  return toBase64(hash);
}
