# AcmeToCasino — Random Number Generation

Code demonstrating the client-server RNG bridge pattern, as referenced in
Chapter 17 (Random Number Generation).

## Files

- **rng.py** — Server-side CSPRNG implementation using Python's `secrets.token_bytes()`,
  backed by the OS entropy source (/dev/urandom on Linux). Converts raw bytes to floats,
  hashes seeds with SHA-256 for the audit trail, and maps outcomes against RTP-weighted
  win tiers.

- **gal-adapter.js** — Client-side JavaScript adapter that intercepts `Math.random()`
  calls in existing casino games and replaces them with server-provided random numbers.
  Pre-fetches batches of 100 random numbers from the `/gal/rng-batch` endpoint, falls
  back to `crypto.getRandomValues()` when the buffer is empty, and only uses the original
  `Math.random()` in fully offline mode.

## How This Maps to Chapter 17

The chapter explains why client-side RNG is fundamentally insecure for real-money gaming
and how to implement server-side alternatives:

1. **The Problem** — `Math.random()` uses a PRNG (xorshift128+ in V8) that is
   predictable if the internal state is known. The adapter solves this by replacing
   it entirely.
2. **CSPRNG on the Server** — `secrets.token_bytes()` draws from the OS entropy pool,
   which is cryptographically secure and unpredictable.
3. **The Bridge Pattern** — The GAL adapter pre-fetches random numbers in batches,
   intercepts `Math.random()` transparently, and also intercepts `localStorage` to
   route balance and RTP reads/writes through the server.
4. **Audit Trail** — Every RNG call on the server produces an SHA-256 hash of the
   seed bytes, enabling post-hoc verification of game fairness.
5. **Graceful Degradation** — If the server is unreachable, the adapter falls back
   to `crypto.getRandomValues()` (Web Crypto API), which is still far superior to
   `Math.random()` for security purposes.
