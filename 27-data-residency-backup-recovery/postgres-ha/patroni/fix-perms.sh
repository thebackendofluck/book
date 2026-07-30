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

# Root shim: fixes volume permissions then drops to postgres user.
# Required because Docker named volumes may be created root:root.

DATA_DIR=/var/lib/postgresql/data
WAL_DIR=/wal-archive

mkdir -p "$DATA_DIR" "$WAL_DIR"
chown -R postgres:postgres "$DATA_DIR" "$WAL_DIR"
chmod 0700 "$DATA_DIR"
chmod 0755 "$WAL_DIR"

exec gosu postgres /entrypoint.sh "$@"
