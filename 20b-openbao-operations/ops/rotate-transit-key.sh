#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Rotate a transit key, rewrap canary ciphertexts, and then actually retire the
# superseded key versions by advancing min_decryption_version.
#
# Rotation alone buys nothing for a crypto period. `transit/keys/<key>/rotate`
# creates a new version and makes it the encryption version, but every earlier
# version stays valid for decryption forever. The key material that encrypted
# last year's data is still live, still usable by anyone who can call
# transit/decrypt, and still inside the compromise blast radius. PCI DSS 3.7.1
# asks for a defined cryptoperiod and for keys to stop being used at the end of
# it; a key version that can still decrypt has not stopped being used.
# min_decryption_version is the control that ends it.
#
# Advancing it is also irreversible: any ciphertext left at an older version
# becomes permanently undecryptable. So this script will not advance it on a
# hunch. It requires a rewrap report from rewrap-all-ciphertexts.py per
# encrypted column, each asserting safe_to_advance, and it verifies the
# retirement afterwards by proving an un-rewrapped ciphertext can no longer be
# decrypted.
#
# Usage:
#   rotate-transit-key.sh <key-name> [--rewrap-report FILE]... [--no-advance]
#
#   --rewrap-report FILE  A JSON report from rewrap-all-ciphertexts.py. Repeat
#                         once per table/column encrypted under this key. All
#                         must assert safe_to_advance for the same key and a
#                         target_version at or above the new version.
#   --no-advance          Rotate and rewrap canaries only. Use when the rewrap
#                         pass has not run yet; the script prints the exact
#                         commands for the remaining steps.
#
# Production note: this script sources lib/common.sh and therefore only ever
# runs against the sandbox. The production sequence is the same three steps and
# is spelled out at the end of a --no-advance run.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

KEY=""
ADVANCE=1
REPORTS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rewrap-report)
      [[ $# -ge 2 ]] || die "--rewrap-report needs a file argument"
      REPORTS+=("$2")
      shift 2
      ;;
    --no-advance)
      ADVANCE=0
      shift
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      [[ -z "$KEY" ]] || die "unexpected extra argument: $1"
      KEY="$1"
      shift
      ;;
  esac
done
KEY=${KEY:-platform-pii}

ensure_sandbox
load_root_token

key_field() {
  "$BAO_BIN" read -format=json "transit/keys/$KEY" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['$1'])"
}

before=$(key_field latest_version 2>/dev/null || echo 0)
log "before rotation: latest_version=$before"

# Canaries 1 and 2 get rewrapped. Canary 3 is deliberately left at the old
# version so that, after min_decryption_version is advanced, we can prove the
# old version really is retired instead of assuming the write took effect.
canaries=()
for i in 1 2 3; do
  pt=$(printf 'canary-%s' "$i" | base64)
  ct=$("$BAO_BIN" write -format=json "transit/encrypt/$KEY" "plaintext=$pt" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["ciphertext"])')
  canaries+=("$ct")
done
log "encrypted ${#canaries[@]} canaries at version $before"
control_ct=${canaries[2]}

# Rotate
log "rotating key $KEY"
"$BAO_BIN" write -f "transit/keys/$KEY/rotate" >/dev/null

after=$(key_field latest_version)
log "after rotation: latest_version=$after"

# Rewrap canaries 1 and 2, and check each one actually moved to $after. A
# rewrap that silently returns the same version would otherwise look like
# success.
log "rewrapping canaries"
rewrap_failures=0
for idx in 0 1; do
  ct=${canaries[$idx]}
  new_ct=$("$BAO_BIN" write -format=json "transit/rewrap/$KEY" "ciphertext=$ct" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["ciphertext"])')
  before_v=$(printf '%s' "$ct" | cut -d: -f2)
  after_v=$(printf '%s' "$new_ct" | cut -d: -f2)
  log "  $before_v -> $after_v"
  if [[ "$after_v" != "v${after}" ]]; then
    log "  ERROR: canary $((idx + 1)) did not reach v${after}"
    rewrap_failures=$((rewrap_failures + 1))
  fi
done
(( rewrap_failures == 0 )) || die "canary rewrap did not reach v${after} — refusing to go further"

#------------------------------------------------------------------------------
# Retirement gate
#------------------------------------------------------------------------------
print_manual_steps() {
  log ""
  log "min_decryption_version was NOT advanced. Versions 1..$((after - 1)) of"
  log "$KEY can still decrypt, so the crypto period has not ended."
  log ""
  log "To finish, for EVERY table/column encrypted under $KEY:"
  log "  ops/rewrap-all-ciphertexts.py --bao-addr \"\$BAO_ADDR\" --bao-token \"\$BAO_TOKEN\" \\"
  log "      --key $KEY --dsn \"\$DSN\" --table <table> --column <column> \\"
  log "      --target-version $after --report-file /var/lib/bao-rotation/<table>.<column>.json"
  log ""
  log "then re-run with every report attached:"
  log "  $0 $KEY --rewrap-report /var/lib/bao-rotation/<table>.<column>.json [--rewrap-report ...]"
}

if (( ADVANCE == 0 )); then
  log "--no-advance requested"
  print_manual_steps
  exit 0
fi

if (( ${#REPORTS[@]} == 0 )); then
  log "no --rewrap-report supplied"
  log "Refusing to advance min_decryption_version on an unverified rewrap:"
  log "doing so would permanently destroy any ciphertext still at an older version."
  print_manual_steps
  exit 0
fi

log "validating ${#REPORTS[@]} rewrap report(s) against key=$KEY target>=$after"
for report in "${REPORTS[@]}"; do
  [[ -r "$report" ]] || die "rewrap report not readable: $report"
  KEY="$KEY" MIN_VERSION="$after" python3 - "$report" <<'PY' || die "rewrap report rejected: $report"
import json, os, sys

path = sys.argv[1]
want_key = os.environ["KEY"]
min_version = int(os.environ["MIN_VERSION"])

with open(path, encoding="utf-8") as handle:
    report = json.load(handle)

problems = []
if report.get("key") != want_key:
    problems.append(f"key={report.get('key')!r}, expected {want_key!r}")
if report.get("dry_run"):
    problems.append("dry_run=true (a dry run changed nothing)")
if not report.get("safe_to_advance"):
    problems.append(
        f"safe_to_advance={report.get('safe_to_advance')!r}, "
        f"rows_remaining_below_target={report.get('rows_remaining_below_target')!r}"
    )
target = report.get("target_version")
if not isinstance(target, int) or target < min_version:
    problems.append(f"target_version={target!r}, need >= {min_version}")

scope = report.get("scope") or {}
if problems:
    print(f"  REJECT {path}: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)

print(f"  OK {path}: {scope.get('table')}.{scope.get('column')} "
      f"rewrapped={report.get('rows_rewrapped')} remaining=0", file=sys.stderr)
PY
done

log "advancing min_decryption_version to $after (retires versions 1..$((after - 1)))"
"$BAO_BIN" write "transit/keys/$KEY/config" "min_decryption_version=$after" >/dev/null

actual_min=$(key_field min_decryption_version)
[[ "$actual_min" == "$after" ]] \
  || die "min_decryption_version is $actual_min, expected $after — retirement did not take effect"
log "min_decryption_version=$actual_min confirmed"

# Prove the retirement instead of trusting the config read. The control canary
# was never rewrapped, so decrypting it must now fail. If it succeeds, the old
# version is still live and the crypto-period claim would be false.
if "$BAO_BIN" write -format=json "transit/decrypt/$KEY" "ciphertext=$control_ct" >/dev/null 2>&1; then
  die "control canary at v${before} still decrypts — versions 1..$((after - 1)) are NOT retired"
fi
log "verified: control canary at v${before} no longer decrypts"

log "rotation + rewrap + retirement complete for $KEY (active version $after, min_decryption_version $actual_min)"
