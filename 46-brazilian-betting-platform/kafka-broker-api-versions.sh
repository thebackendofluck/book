#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# kafka-broker-api-versions.sh
#
# Compatibility wrapper for the kafka-broker-api-versions command.
#
# Confluent Platform 7.6.0 removed the .sh extension from all Kafka CLI
# commands — the binary is now at /usr/bin/kafka-broker-api-versions with no
# extension. Older images expose it as kafka-broker-api-versions.sh in
# /usr/bin or /usr/local/bin.
#
# This wrapper tries the no-extension binary first (Confluent 7.6.0+) and
# falls back to the .sh-suffixed version for older images. The health check
# in docker-compose.yml should reference this wrapper so the same compose
# file works across image versions:
#
#   healthcheck:
#     test: ["CMD-SHELL",
#            "kafka-broker-api-versions.sh --bootstrap-server localhost:29092"]
#
# Chapter 46: Brazilian Betting Platform — E2E validation fix (issue 3).

set -euo pipefail

# ── Try the new binary first (Confluent Platform >= 7.6.0) ────────────────
if command -v kafka-broker-api-versions >/dev/null 2>&1; then
    exec kafka-broker-api-versions "$@"
fi

# ── Fall back to the .sh-suffixed binary (Confluent Platform < 7.6.0) ─────
if command -v kafka-broker-api-versions.sh >/dev/null 2>&1 && \
   [[ "${0##*/}" != "kafka-broker-api-versions.sh" ]]; then
    exec kafka-broker-api-versions.sh "$@"
fi

# ── Last resort: look for the binary under the Kafka/Confluent bin dirs ───
for dir in /usr/bin /usr/local/bin /opt/kafka/bin /opt/confluent/bin; do
    if [[ -x "${dir}/kafka-broker-api-versions" ]]; then
        exec "${dir}/kafka-broker-api-versions" "$@"
    fi
    if [[ -x "${dir}/kafka-broker-api-versions.sh" ]]; then
        exec "${dir}/kafka-broker-api-versions.sh" "$@"
    fi
done

echo "ERROR: kafka-broker-api-versions not found in PATH or standard locations." >&2
echo "Confluent Platform >= 7.6.0: binary is 'kafka-broker-api-versions' (no .sh)" >&2
echo "Confluent Platform <  7.6.0: binary is 'kafka-broker-api-versions.sh'" >&2
exit 1
