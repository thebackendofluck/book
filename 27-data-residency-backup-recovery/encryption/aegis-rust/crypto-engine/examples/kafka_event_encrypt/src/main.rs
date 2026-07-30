// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Kafka event encryption pipeline.
//!
//! Reads JSON events (one per line) from stdin, encrypts each with
//! AEGIS-128L, and writes base64-encoded wire payloads to stdout.
//!
//! Key is read from env var `CRYPTO_KEY_HEX` (32 hex chars = 16 bytes).
//! If unset, a random key is generated and printed to stderr.
//!
//! Counter-based nonces ensure no reuse across a single process run.
//! Set `PRODUCER_ID` env var to distinguish multiple producers
//! (e.g. kafka partition id).

use crypto_engine::{CryptoEngine, NonceGenerator};
use std::io::{self, BufRead, Write};
use std::time::Instant;

const B64: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

fn base64_encode(input: &[u8]) -> String {
    let mut out = String::with_capacity((input.len() + 2) / 3 * 4);
    let mut i = 0;
    while i + 3 <= input.len() {
        let n = ((input[i] as u32) << 16) | ((input[i + 1] as u32) << 8) | (input[i + 2] as u32);
        out.push(B64[((n >> 18) & 0x3F) as usize] as char);
        out.push(B64[((n >> 12) & 0x3F) as usize] as char);
        out.push(B64[((n >> 6) & 0x3F) as usize] as char);
        out.push(B64[(n & 0x3F) as usize] as char);
        i += 3;
    }
    let rem = input.len() - i;
    if rem == 1 {
        let n = (input[i] as u32) << 16;
        out.push(B64[((n >> 18) & 0x3F) as usize] as char);
        out.push(B64[((n >> 12) & 0x3F) as usize] as char);
        out.push('=');
        out.push('=');
    } else if rem == 2 {
        let n = ((input[i] as u32) << 16) | ((input[i + 1] as u32) << 8);
        out.push(B64[((n >> 18) & 0x3F) as usize] as char);
        out.push(B64[((n >> 12) & 0x3F) as usize] as char);
        out.push(B64[((n >> 6) & 0x3F) as usize] as char);
        out.push('=');
    }
    out
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    let mut v = Vec::with_capacity(s.len() / 2);
    for chunk in s.as_bytes().chunks(2) {
        let hi = (chunk[0] as char).to_digit(16)?;
        let lo = (chunk[1] as char).to_digit(16)?;
        v.push(((hi << 4) | lo) as u8);
    }
    Some(v)
}

fn main() -> io::Result<()> {
    let key_bytes: [u8; 16] = match std::env::var("CRYPTO_KEY_HEX") {
        Ok(hex) => {
            let b = hex_decode(&hex).expect("CRYPTO_KEY_HEX must be 32 hex chars");
            assert_eq!(b.len(), 16, "expected 16-byte key, got {}", b.len());
            let mut k = [0u8; 16];
            k.copy_from_slice(&b);
            k
        }
        Err(_) => {
            let k = crypto_engine::random_key_16();
            eprintln!(
                "# generated ephemeral key: {}",
                k.iter()
                    .map(|b| format!("{:02x}", b))
                    .collect::<String>()
            );
            k
        }
    };

    let producer_id: u64 = std::env::var("PRODUCER_ID")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1);

    let engine = CryptoEngine::new_aegis128l(&key_bytes).expect("engine init");
    let nonces = NonceGenerator::new(16, producer_id);

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut count: u64 = 0;
    let start = Instant::now();

    for line in stdin.lock().lines() {
        let line = line?;
        if line.is_empty() {
            continue;
        }
        let nonce = nonces.next();
        // Use topic+partition context as AAD (here synthetic)
        let aad = format!("producer={}:seq={}", producer_id, nonces.current());
        let payload = engine
            .encrypt_with_nonce(line.as_bytes(), aad.as_bytes(), &nonce)
            .expect("encrypt");
        writeln!(out, "{}", base64_encode(&payload))?;
        count += 1;
    }

    let secs = start.elapsed().as_secs_f64();
    if secs > 0.0 {
        eprintln!(
            "# encrypted {} events in {:.3}s ({:.0} events/sec)",
            count,
            secs,
            count as f64 / secs
        );
    }
    Ok(())
}
