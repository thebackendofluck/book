#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Toggle a feature flag in Redis — fastest incident mitigation path (< 5 seconds)
# Section 6.2 — Feature flag toggle
#
# Usage:
#   hotfix-feature-flag.sh --change-ref INC-1234 \
#       --namespace casino-prod --flag new_slot_rtp_calculator --value false \
#       --ttl 3600 --i-accept-production-impact
#
# This is the preferred first action in an incident, because disabling a flag
# turns a code path off rather than introducing new code, and it is reversible.
# It still gets the full change reference, production override and typed
# namespace confirmation: "disabled the wrong flag in the wrong environment" is
# the usual way this goes wrong under time pressure, and a flag that suppresses
# a player-facing feature is a change a regulator can ask you to account for.
#
# The TTL matters. A flag set with no expiry becomes permanent undocumented
# state that nobody remembers during the next incident. Default is one hour,
# which is long enough to ship a real fix and short enough to force the
# conversation if you have not.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=hotfix-guard.sh
source "$(dirname "$0")/hotfix-guard.sh"

hotfix_parse_flags "$@"
set -- "${HOTFIX_REMAINING_ARGS[@]+"${HOTFIX_REMAINING_ARGS[@]}"}"

# Fail before touching the cluster at all, not after the first read.
hotfix_require_change_ref

NAMESPACE=""
FLAG=""
VALUE=""
TTL="3600"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --namespace|-n) NAMESPACE="$2"; shift 2 ;;
        --flag) FLAG="$2"; shift 2 ;;
        --value) VALUE="$2"; shift 2 ;;
        --ttl) TTL="$2"; shift 2 ;;
        *) hotfix_die "unknown argument: $1" ;;
    esac
done

[ -n "$NAMESPACE" ] || hotfix_die "--namespace is required"
[ -n "$FLAG" ] || hotfix_die "--flag is required"
[ -n "$VALUE" ] || hotfix_die "--value is required (true or false)"

case "$VALUE" in
    true|false) ;;
    *) hotfix_die "--value must be true or false, got '$VALUE'" ;;
esac

printf '%s' "$TTL" | grep -Eq '^[0-9]+$' || hotfix_die "--ttl must be seconds"
[ "$TTL" -gt 0 ] || hotfix_die "--ttl must be greater than zero.
   A flag with no expiry is permanent state that no runbook records."

hotfix_require_production_override "$NAMESPACE"

REDIS_HOST="${REDIS_HOST:-redis.${NAMESPACE}.svc.cluster.local}"
REDIS_PORT="${REDIS_PORT:-6379}"
API_HOST="${API_HOST:-player-service.${NAMESPACE}.svc.cluster.local:8000}"

PREVIOUS=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" GET "feature:${FLAG}" 2>/dev/null || true)
printf 'hotfix: feature:%s currently %s\n' "$FLAG" "${PREVIOUS:-<unset>}" >&2

# Turning a flag ON enables a code path rather than containing one, which is a
# deploy in everything but name. Say so in the audit record, because "toggled a
# flag" and "enabled an untested path in production" read very differently to
# whoever reviews this later.
if [ "$VALUE" = "true" ]; then
    printf 'hotfix: %s=true ENABLES a code path. If this is not a re-enable after a verified fix, stop.\n' \
        "$FLAG" >&2
    ACTION="enable feature:${FLAG} (was ${PREVIOUS:-<unset>}) ttl=${TTL}s"
else
    ACTION="disable feature:${FLAG} (was ${PREVIOUS:-<unset>}) ttl=${TTL}s"
fi

hotfix_authorize "$NAMESPACE" "$ACTION"

redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" \
    SET "feature:${FLAG}" "$VALUE" EX "$TTL"

# Confirm the write landed and read it back from the service, not from Redis.
# Services poll on a one-second interval, so a value that is in Redis but not
# yet in the API means propagation, not failure.
sleep 2
if curl -fsS "http://${API_HOST}/api/v1/feature-flags" \
    | python3 -c "
import json, sys
flags = json.load(sys.stdin)
observed = str(flags.get('${FLAG}', '<absent>')).lower()
if observed != '${VALUE}':
    sys.exit(f'flag ${FLAG} reads back as {observed}, expected ${VALUE}')
print('hotfix: ${FLAG} confirmed ${VALUE} via player-service')
"; then
    hotfix_audit "$NAMESPACE" "$ACTION" "applied-and-verified"
else
    hotfix_audit "$NAMESPACE" "$ACTION" "applied-but-unverified"
    hotfix_die "wrote feature:${FLAG} but could not confirm it through player-service.
   The Redis write may have landed. Check before writing it again."
fi

printf 'hotfix: feature:%s expires in %ss. Ship the real fix or extend deliberately.\n' \
    "$FLAG" "$TTL" >&2
