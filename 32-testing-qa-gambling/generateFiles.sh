#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# generateFiles.sh -- Split user pool across N Gatling test nodes
# Used in distributed performance testing to partition test users
# Each node gets its own slice with the header re-prepended
# Usage: ./generateFiles.sh <number_of_nodes>
# Example: ./generateFiles.sh 10  (creates users_aa through users_aj)

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <number_of_nodes>"
    echo "Example: $0 10"
    exit 1
fi

NODES="$1"
INPUT_FILE="users.csv"

if [[ ! -f "$INPUT_FILE" ]]; then
    echo "Error: $INPUT_FILE not found"
    echo "Generate users first with: python3 generate_users.py --count 10000 > users.csv"
    exit 1
fi

# Remove existing split files
rm -f users_??

# Split the user pool across N nodes (l = line-based split)
split --number="l/${NODES}" "$INPUT_FILE" users_

# Re-prepend the CSV header to each split file
# The header line is the first line of the original file
HEADER=$(head -1 "$INPUT_FILE")
for f in users_??; do
    # Only add header if the file doesn't already have it
    if ! head -1 "$f" | grep -q "^username"; then
        echo "$HEADER" | cat - "$f" > /tmp/out && mv /tmp/out "$f"
    fi
done

echo "Split $INPUT_FILE into ${NODES} files:"
for f in users_??; do
    LINE_COUNT=$(wc -l < "$f")
    echo "  $f: $((LINE_COUNT - 1)) users"
done
