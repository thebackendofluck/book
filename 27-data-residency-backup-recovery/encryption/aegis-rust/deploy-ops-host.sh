#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Deploy crypto-engine to the ops-host benchmark VM and run the
# throughput benchmark there.
#
# Usage:  ./deploy-ops-host.sh [host]
#         defaults to admin@10.0.10.55

set -euo pipefail

HOST="${1:-admin@10.0.10.55}"
REMOTE_DIR="~/crypto-engine-bench"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[1/4] syncing source tree to ${HOST}:${REMOTE_DIR}"
rsync -az --delete \
  --exclude 'crypto-engine/target' \
  --exclude '.git' \
  "${LOCAL_DIR}/" "${HOST}:${REMOTE_DIR}/"

echo "[2/4] installing rustc (if missing) + building release"
ssh "${HOST}" bash -se <<'EOS'
set -euo pipefail
if ! command -v cargo >/dev/null 2>&1; then
  echo "  installing rustup toolchain..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi
source "$HOME/.cargo/env" 2>/dev/null || true
cd ~/crypto-engine-bench/crypto-engine
cargo build --release
cargo test --release 2>&1 | tail -20
EOS

echo "[3/4] printing CPU features"
ssh "${HOST}" "grep -m1 'model name' /proc/cpuinfo; grep -Eo 'aes|avx|avx2|avx512|vaes|pclmulqdq' /proc/cpuinfo | sort -u | tr '\n' ' '; echo"

echo "[4/4] running benchmark"
ssh "${HOST}" "source \$HOME/.cargo/env 2>/dev/null; cd ~/crypto-engine-bench/crypto-engine && ./target/release/bench"

echo
echo "done. Compare AEGIS-128L throughput above to PostgreSQL pgcrypto"
echo "(typical AES-256-CBC in pgcrypto runs at ~0.3 GB/s on the same CPU)."
