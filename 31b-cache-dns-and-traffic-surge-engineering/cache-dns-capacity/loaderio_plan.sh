#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 31b, Cache, DNS, and Traffic Surge Engineering.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Generate or execute loader.io API calls for external edge load tests.

set -euo pipefail

HOST=""
URL=""
NAME="edge-cache-canary"
TYPE="maintain-load"
DURATION="300"
INITIAL="100"
TOTAL="1000"
TIMEOUT_MS="10000"
ERROR_THRESHOLD="2"
METHOD="GET"
TEST_ID=""
EXECUTE="false"

usage() {
  sed -n '1,120p' "$0"
  cat <<'USAGE'

Required:
  --host <hostname>      Host registered in loader.io
  --url <url>            URL to test

Optional:
  --name <name>          Test name (default: edge-cache-canary)
  --type <type>          per-test, per-second, or maintain-load
  --duration <seconds>   Test duration in seconds
  --initial <clients>    Initial clients
  --total <clients>      Total clients
  --timeout <ms>         Request timeout in milliseconds
  --error-threshold <n>  Error threshold percentage
  --method <method>      HTTP method (default: GET)
  --test-id <id>         Existing test id for run/stop/results examples
  --execute              Execute create-host and create-test calls

Environment:
  LOADERIO_API_KEY       Required only with --execute
USAGE
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

api() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"

  if [[ "$EXECUTE" == "true" ]]; then
    if [[ -z "${LOADERIO_API_KEY:-}" ]]; then
      echo "LOADERIO_API_KEY is required with --execute" >&2
      exit 1
    fi
    if [[ -n "$payload" ]]; then
      curl -sS -X "$method" "https://api.loader.io/v2/${path}" \
        -H "loaderio-auth: ${LOADERIO_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$payload"
      printf '\n'
    else
      curl -sS -X "$method" "https://api.loader.io/v2/${path}" \
        -H "loaderio-auth: ${LOADERIO_API_KEY}"
      printf '\n'
    fi
  else
    printf 'curl -sS -X %s https://api.loader.io/v2/%s \\\n' "$method" "$path"
    # shellcheck disable=SC2016
    printf '  -H "loaderio-auth: $LOADERIO_API_KEY"'
    if [[ -n "$payload" ]]; then
      printf ' \\\n  -H "Content-Type: application/json" \\\n'
      printf "  -d '%s'" "$payload"
    fi
    printf '\n\n'
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --url)
      URL="${2:-}"
      shift 2
      ;;
    --name)
      NAME="${2:-}"
      shift 2
      ;;
    --type)
      TYPE="${2:-}"
      shift 2
      ;;
    --duration)
      DURATION="${2:-}"
      shift 2
      ;;
    --initial)
      INITIAL="${2:-}"
      shift 2
      ;;
    --total)
      TOTAL="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT_MS="${2:-}"
      shift 2
      ;;
    --error-threshold)
      ERROR_THRESHOLD="${2:-}"
      shift 2
      ;;
    --method)
      METHOD="${2:-}"
      shift 2
      ;;
    --test-id)
      TEST_ID="${2:-}"
      shift 2
      ;;
    --execute)
      EXECUTE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

case "$TYPE" in
  per-test|per-second|maintain-load) ;;
  *)
    echo "--type must be per-test, per-second, or maintain-load" >&2
    exit 1
    ;;
esac

if [[ -z "$HOST" || -z "$URL" ]]; then
  usage >&2
  exit 1
fi

host_payload="$(printf '{"app":"%s"}' "$(json_escape "$HOST")")"
test_payload="$(cat <<JSON
{"test_type":"$(json_escape "$TYPE")","name":"$(json_escape "$NAME")","urls":[{"url":"$(json_escape "$URL")","request_type":"$(json_escape "$METHOD")","headers":{"X-Synthetic-Traffic":"loaderio-chapter-31b"}}],"duration":${DURATION},"initial":${INITIAL},"total":${TOTAL},"timeout":${TIMEOUT_MS},"error_threshold":${ERROR_THRESHOLD}}
JSON
)"

if [[ "$EXECUTE" != "true" ]]; then
  cat <<PLAN
# loader.io plan
#
# 1. Export LOADERIO_API_KEY in your shell.
# 2. Create/register the host.
# 3. Add HTTP or DNS verification in loader.io UI/API and verify the host.
# 4. Create the test.
# 5. Run the test only inside the approved window.

PLAN
fi

api POST apps "$host_payload"

cat <<VERIFY
# After loader.io returns the host id, verify it:

VERIFY
if [[ "$EXECUTE" == "true" ]]; then
  echo "# Verification requires the host id returned above."
else
  api POST "apps/<host_id>/verify"
fi

api POST tests "$test_payload"

if [[ -n "$TEST_ID" ]]; then
  api PUT "tests/${TEST_ID}/run"
  api PUT "tests/${TEST_ID}/stop"
  api GET "tests/${TEST_ID}/results"
else
  cat <<NEXT
# After loader.io returns the test id:
# curl -sS -X PUT https://api.loader.io/v2/tests/<test_id>/run -H "loaderio-auth: \$LOADERIO_API_KEY"
# curl -sS -X PUT https://api.loader.io/v2/tests/<test_id>/stop -H "loaderio-auth: \$LOADERIO_API_KEY"
# curl -sS https://api.loader.io/v2/tests/<test_id>/results -H "loaderio-auth: \$LOADERIO_API_KEY"
NEXT
fi
