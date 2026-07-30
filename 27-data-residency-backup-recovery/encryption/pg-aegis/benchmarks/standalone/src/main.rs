// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Standalone throughput benchmark for the AEAD primitives that
//! pg_aegis wraps. Measures raw encrypt throughput at PII-typical
//! message sizes. Numbers here are an upper bound on what pg_aegis
//! can achieve inside PostgreSQL (SPI + heap IO add overhead).

use std::time::{Duration, Instant};
use rand::RngCore;

const SIZES: &[usize] = &[32, 64, 128, 256, 512, 1024, 4096, 16384];
const DURATION: Duration = Duration::from_millis(1500);

fn fmt_mbs(bytes: u64, secs: f64) -> f64 { bytes as f64 / secs / 1_048_576.0 }

fn bench<F: FnMut() -> usize>(mut op: F) -> (u64, u64, f64) {
    let mut ops = 0u64;
    let mut bytes = 0u64;
    let start = Instant::now();
    while start.elapsed() < DURATION {
        for _ in 0..256 {
            bytes += op() as u64;
            ops += 1;
        }
    }
    let secs = start.elapsed().as_secs_f64();
    (ops, bytes, secs)
}

fn bench_aegis128l(msg: &[u8]) {
    use aegis::aegis128l::Aegis128L;
    let key = [0x11u8; 16];
    let mut nonce = [0u8; 16];
    let (ops, bytes, secs) = bench(|| {
        rand::thread_rng().fill_bytes(&mut nonce[..8]);
        let c = Aegis128L::<16>::new(&key, &nonce);
        let (ct, _tag) = c.encrypt(msg, b"");
        ct.len()
    });
    println!("  AEGIS-128L       {:>8} B  {:>10} ops  {:>8.1} MB/s  ({:.0}ns/op)",
             msg.len(), ops, fmt_mbs(bytes, secs), secs * 1e9 / ops as f64);
}

fn bench_chacha(msg: &[u8]) {
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce, KeyInit};
    use chacha20poly1305::aead::Aead;
    let key = [0x22u8; 32];
    let c = ChaCha20Poly1305::new(Key::from_slice(&key));
    let mut nonce = [0u8; 12];
    let (ops, bytes, secs) = bench(|| {
        rand::thread_rng().fill_bytes(&mut nonce[..8]);
        let ct = c.encrypt(Nonce::from_slice(&nonce), msg).unwrap();
        ct.len()
    });
    println!("  ChaCha20-Poly1305 {:>7} B  {:>10} ops  {:>8.1} MB/s  ({:.0}ns/op)",
             msg.len(), ops, fmt_mbs(bytes, secs), secs * 1e9 / ops as f64);
}

fn bench_aes_gcm(msg: &[u8]) {
    use aes_gcm::{Aes256Gcm, Key, Nonce, KeyInit};
    use aes_gcm::aead::Aead;
    let key = [0x33u8; 32];
    let c = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(&key));
    let mut nonce = [0u8; 12];
    let (ops, bytes, secs) = bench(|| {
        rand::thread_rng().fill_bytes(&mut nonce[..8]);
        let ct = c.encrypt(Nonce::from_slice(&nonce), msg).unwrap();
        ct.len()
    });
    println!("  AES-256-GCM      {:>8} B  {:>10} ops  {:>8.1} MB/s  ({:.0}ns/op)",
             msg.len(), ops, fmt_mbs(bytes, secs), secs * 1e9 / ops as f64);
}

fn main() {
    println!("pg_aegis AEAD throughput benchmark");
    println!("==================================");
    println!("host: {}", std::env::consts::ARCH);
    println!();
    for &n in SIZES {
        let msg = vec![0x42u8; n];
        println!("payload = {} B", n);
        bench_aegis128l(&msg);
        bench_chacha(&msg);
        bench_aes_gcm(&msg);
        println!();
    }
}
