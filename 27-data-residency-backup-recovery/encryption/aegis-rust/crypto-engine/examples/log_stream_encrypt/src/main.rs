// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Log stream encryption.
//!
//! Reads lines from stdin, batches them into 64KB blocks, encrypts each
//! block with AEGIS-128L using a counter-based nonce, and writes each
//! block (length-prefixed, u32 BE) to stdout.
//!
//! Output wire format:
//!   [u32 BE block_len] [payload bytes]* ...
//!
//! The 64KB block size amortises AEAD overhead for maximum throughput.

use crypto_engine::{CryptoEngine, NonceGenerator};
use std::io::{self, BufRead, Read, Write};
use std::time::Instant;

const BLOCK_SIZE: usize = 64 * 1024;

fn main() -> io::Result<()> {
    let key_bytes: [u8; 16] = std::env::var("CRYPTO_KEY_HEX")
        .ok()
        .and_then(|h| {
            if h.len() != 32 {
                return None;
            }
            let mut k = [0u8; 16];
            for (i, chunk) in h.as_bytes().chunks(2).enumerate() {
                let hi = (chunk[0] as char).to_digit(16)?;
                let lo = (chunk[1] as char).to_digit(16)?;
                k[i] = ((hi << 4) | lo) as u8;
            }
            Some(k)
        })
        .unwrap_or_else(|| {
            let k = crypto_engine::random_key_16();
            eprintln!(
                "# generated ephemeral key: {}",
                k.iter()
                    .map(|b| format!("{:02x}", b))
                    .collect::<String>()
            );
            k
        });

    let stream_id: u64 = std::env::var("STREAM_ID")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1);

    let engine = CryptoEngine::new_aegis128l(&key_bytes).expect("engine init");
    let nonces = NonceGenerator::new(16, stream_id);

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut reader = stdin.lock();

    let mut buf = Vec::with_capacity(BLOCK_SIZE);
    let mut line = String::new();
    let mut total_bytes: u64 = 0;
    let mut blocks: u64 = 0;
    let start = Instant::now();

    let use_line_mode = std::env::var("MODE").ok().as_deref() == Some("lines");

    if use_line_mode {
        loop {
            line.clear();
            let n = reader.read_line(&mut line)?;
            if n == 0 {
                break;
            }
            if buf.len() + line.len() > BLOCK_SIZE && !buf.is_empty() {
                flush_block(&mut buf, &engine, &nonces, stream_id, &mut out)?;
                blocks += 1;
            }
            buf.extend_from_slice(line.as_bytes());
            total_bytes += line.len() as u64;
        }
    } else {
        let mut tmp = vec![0u8; BLOCK_SIZE];
        loop {
            let n = reader.read(&mut tmp)?;
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&tmp[..n]);
            total_bytes += n as u64;
            while buf.len() >= BLOCK_SIZE {
                let mut chunk: Vec<u8> = buf.drain(..BLOCK_SIZE).collect();
                flush_block(&mut chunk, &engine, &nonces, stream_id, &mut out)?;
                blocks += 1;
            }
        }
    }

    if !buf.is_empty() {
        flush_block(&mut buf, &engine, &nonces, stream_id, &mut out)?;
        blocks += 1;
    }

    let secs = start.elapsed().as_secs_f64();
    if secs > 0.0 {
        eprintln!(
            "# {} blocks, {} bytes in {:.3}s ({:.2} MB/s)",
            blocks,
            total_bytes,
            secs,
            total_bytes as f64 / secs / 1e6
        );
    }
    Ok(())
}

fn flush_block(
    block: &mut Vec<u8>,
    engine: &CryptoEngine,
    nonces: &NonceGenerator,
    stream_id: u64,
    out: &mut dyn Write,
) -> io::Result<()> {
    let nonce = nonces.next();
    let aad = format!("stream={}:block={}", stream_id, nonces.current());
    let payload = engine
        .encrypt_with_nonce(block, aad.as_bytes(), &nonce)
        .expect("encrypt");
    let len = payload.len() as u32;
    out.write_all(&len.to_be_bytes())?;
    out.write_all(&payload)?;
    block.clear();
    Ok(())
}
