// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Standalone throughput benchmark.
//!
//! Measures encryption throughput for each supported algorithm across
//! a range of message sizes. Prints a summary to stdout.

use crypto_engine::{CryptoEngine, NonceGenerator};
use std::time::Instant;

const SIZES: &[usize] = &[64, 256, 1024, 4096, 16384, 65536, 262144, 1048576];
const DURATION_SECS: f64 = 1.5;

fn bench_encrypt(label: &str, engine: &CryptoEngine, msg: &[u8], nonce_len: usize) {
    let gen = NonceGenerator::new(nonce_len, 1);
    // Warmup
    for _ in 0..100 {
        let n = gen.next();
        let _ = engine.encrypt_with_nonce(msg, b"", &n).unwrap();
    }
    let start = Instant::now();
    let mut ops: u64 = 0;
    let mut bytes: u64 = 0;
    while start.elapsed().as_secs_f64() < DURATION_SECS {
        for _ in 0..1024 {
            let n = gen.next();
            let p = engine.encrypt_with_nonce(msg, b"", &n).unwrap();
            bytes += p.len() as u64;
            ops += 1;
        }
    }
    let secs = start.elapsed().as_secs_f64();
    let gbps = (bytes as f64 / secs) / 1e9;
    let mops = ops as f64 / secs / 1e6;
    println!(
        "{:>24} {:>10} B  {:>10.3} GB/s  {:>8.2} Mops/s",
        label,
        msg.len(),
        gbps,
        mops
    );
}

fn main() {
    println!("crypto-engine benchmark");
    println!(
        "AES-NI available: {}   recommended: {:?}",
        crypto_engine::has_aes_hardware(),
        crypto_engine::recommended_algorithm()
    );
    println!();
    println!(
        "{:>24} {:>10}    {:>10}     {:>8}",
        "algorithm", "msg_size", "throughput", "ops/sec"
    );
    println!("{}", "-".repeat(72));

    let k16 = [0x5Au8; 16];
    let k32 = [0x5Au8; 32];
    let e_aegis128l = CryptoEngine::new_aegis128l(&k16).unwrap();
    let e_aegis256 = CryptoEngine::new_aegis256(&k32).unwrap();
    let e_aesgcm = CryptoEngine::new_aes256gcm(&k32).unwrap();
    let e_chacha = CryptoEngine::new_chacha20poly1305(&k32).unwrap();

    for &sz in SIZES {
        let msg = vec![0x42u8; sz];
        bench_encrypt("AEGIS-128L", &e_aegis128l, &msg, 16);
        bench_encrypt("AEGIS-256", &e_aegis256, &msg, 32);
        bench_encrypt("AES-256-GCM", &e_aesgcm, &msg, 12);
        bench_encrypt("ChaCha20-Poly1305", &e_chacha, &msg, 12);
        println!();
    }
}
