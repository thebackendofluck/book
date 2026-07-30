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

# Shared guardrails for every hotfix path in Section 6.2.
# Source this from any script that mutates a running environment:
#
#   source "$(dirname "$0")/hotfix-guard.sh"
#
# What it enforces:
#   1. A change reference (ticket or incident id) on every invocation.
#   2. An append-only audit record written before the mutation, not after.
#   3. Typed confirmation of the target namespace, so a wrong -n is caught.
#   4. Refusal to touch a production namespace unless the operator passes
#      --i-accept-production-impact as an argument, where it lands in shell
#      history, CI logs and the audit record.
#
# None of this makes an unreviewed production change safe. It makes it
# attributable, which is the minimum a licensed operator can defend.

set -euo pipefail

HOTFIX_AUDIT_LOG="${HOTFIX_AUDIT_LOG:-/var/log/casino/hotfix-audit.log}"
HOTFIX_PRODUCTION_NAMESPACES="${HOTFIX_PRODUCTION_NAMESPACES:-casino-prod casino-prod-eu casino-prod-br}"

# Ticket or incident reference: PROJ-123, INC-4567, CHG-89.
HOTFIX_CHANGE_REF_PATTERN="${HOTFIX_CHANGE_REF_PATTERN:-^(INC|CHG|[A-Z][A-Z0-9]+)-[0-9]+$}"

hotfix_die() {
    printf 'hotfix: %s\n' "$*" >&2
    exit 1
}

# Pull --change-ref and --i-accept-production-impact out of "$@". Callers pass
# everything through and read the exported results.
#
#   hotfix_parse_flags "$@"
#   set -- "${HOTFIX_REMAINING_ARGS[@]}"
hotfix_parse_flags() {
    HOTFIX_CHANGE_REF=""
    HOTFIX_PRODUCTION_OVERRIDE="no"
    HOTFIX_INVOCATION="$0 $*"
    HOTFIX_REMAINING_ARGS=()

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --change-ref)
                [ "$#" -ge 2 ] || hotfix_die "--change-ref needs a value"
                HOTFIX_CHANGE_REF="$2"
                shift 2
                ;;
            --change-ref=*)
                HOTFIX_CHANGE_REF="${1#*=}"
                shift
                ;;
            --i-accept-production-impact)
                HOTFIX_PRODUCTION_OVERRIDE="yes"
                shift
                ;;
            *)
                HOTFIX_REMAINING_ARGS+=("$1")
                shift
                ;;
        esac
    done
}

hotfix_require_change_ref() {
    [ -n "${HOTFIX_CHANGE_REF:-}" ] || hotfix_die \
        "no change reference. Pass --change-ref INC-1234 (or CHG-/PROJ- id).
   Every production mutation needs something a regulator can trace back to a
   decision. If no ticket exists yet, open one; it takes less time than the
   rollout."

    printf '%s' "$HOTFIX_CHANGE_REF" | grep -Eq "$HOTFIX_CHANGE_REF_PATTERN" \
        || hotfix_die "change reference '$HOTFIX_CHANGE_REF' does not look like a ticket id (expected e.g. INC-1234)"
}

hotfix_namespace_is_production() {
    local candidate="$1" known
    for known in $HOTFIX_PRODUCTION_NAMESPACES; do
        [ "$candidate" = "$known" ] && return 0
    done
    return 1
}

# hotfix_require_production_override <namespace>
# Call this straight after argument parsing, before any cluster read. Failing
# early keeps a refused production attempt from doing work and from filling the
# audit log with records for a change that was never going to happen.
hotfix_require_production_override() {
    local namespace="$1"

    hotfix_namespace_is_production "$namespace" || return 0
    [ "$HOTFIX_PRODUCTION_OVERRIDE" = "yes" ] && return 0

    hotfix_die "namespace '$namespace' is production.
   Re-run with --i-accept-production-impact if that is genuinely what you mean.
   The flag is an argument rather than an environment variable on purpose: it
   shows up in shell history, in CI logs and in the audit record.
   Prefer a feature-flag disable or 'kubectl rollout undo' first: both are
   reversible, and neither changes what the certified build does."
}

# hotfix_authorize <namespace> <action-description>
# Call this immediately before the mutating command. It records the intent and
# then blocks for confirmation, so an aborted run still leaves a trace of what
# somebody was about to do.
hotfix_authorize() {
    local namespace="$1" action="$2"

    # Both already checked at startup; re-checked here so that a caller which
    # forgets the early gate still cannot mutate production unannounced.
    hotfix_require_change_ref
    hotfix_require_production_override "$namespace"

    hotfix_audit "$namespace" "$action" "attempted"

    printf '\n'
    printf '  namespace : %s%s\n' "$namespace" \
        "$(hotfix_namespace_is_production "$namespace" && printf '  [PRODUCTION]')"
    printf '  action    : %s\n' "$action"
    printf '  change    : %s\n' "$HOTFIX_CHANGE_REF"
    printf '  operator  : %s\n' "$(hotfix_operator)"
    printf '\n'
    printf 'Type the namespace to confirm: '

    local typed
    read -r typed
    [ "$typed" = "$namespace" ] || {
        hotfix_audit "$namespace" "$action" "aborted-at-confirmation"
        hotfix_die "confirmation did not match '$namespace'; nothing was changed"
    }

    hotfix_audit "$namespace" "$action" "authorized"
}

hotfix_operator() {
    printf '%s@%s' "${SUDO_USER:-${USER:-unknown}}" "$(hostname -s 2>/dev/null || printf 'unknown')"
}

# hotfix_audit <namespace> <action> <outcome>
# One JSON object per line, appended. Ship this to the same log pipeline as the
# application audit trail (Chapter 26) so it survives the host.
hotfix_audit() {
    local namespace="$1" action="$2" outcome="$3"
    local record

    record=$(printf '{"ts":"%s","outcome":"%s","namespace":"%s","action":"%s","change_ref":"%s","operator":"%s","production_override":"%s","invocation":"%s"}' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$outcome" \
        "$namespace" \
        "$action" \
        "${HOTFIX_CHANGE_REF:-none}" \
        "$(hotfix_operator)" \
        "$HOTFIX_PRODUCTION_OVERRIDE" \
        "${HOTFIX_INVOCATION:-unknown}")

    if ! { mkdir -p "$(dirname "$HOTFIX_AUDIT_LOG")" && printf '%s\n' "$record" >>"$HOTFIX_AUDIT_LOG"; } 2>/dev/null; then
        hotfix_die "cannot write audit log $HOTFIX_AUDIT_LOG.
   Refusing to make an unrecorded production change. Set HOTFIX_AUDIT_LOG to a
   writable path that is shipped off the host."
    fi

    printf '%s\n' "$record" >&2
}

# hotfix_verify_image_provenance <image-ref>
# A hotfix image must be something the pipeline produced, not something a
# laptop produced. Prefer a cosign signature; accept CI provenance labels as a
# fallback for registries without signing yet. Refuse anything unattested.
hotfix_verify_image_provenance() {
    local image="$1"

    if command -v cosign >/dev/null 2>&1 && [ -n "${COSIGN_PUBLIC_KEY:-}" ]; then
        cosign verify --key "$COSIGN_PUBLIC_KEY" "$image" >/dev/null 2>&1 \
            && { printf 'hotfix: cosign signature verified for %s\n' "$image" >&2; return 0; }
        hotfix_die "cosign could not verify $image against $COSIGN_PUBLIC_KEY.
   An unsigned image must not reach a production namespace."
    fi

    command -v crane >/dev/null 2>&1 \
        || hotfix_die "cannot verify provenance of $image: neither cosign (with COSIGN_PUBLIC_KEY) nor crane is available"

    local config provenance
    config=$(crane config "$image" 2>/dev/null) \
        || hotfix_die "cannot read image config for $image; is it pushed?"

    # Unreadable metadata is an unverified image, so parse failures must exit
    # non-zero here rather than surface as an empty label further down.
    provenance=$(printf '%s' "$config" | python3 -c '
import json, sys

try:
    labels = json.load(sys.stdin).get("config", {}).get("Labels") or {}
except (json.JSONDecodeError, AttributeError) as exc:
    sys.exit(f"image config is not readable JSON: {exc}")

required = ("ci.pipeline-id", "org.opencontainers.image.revision")
missing = [name for name in required if not labels.get(name)]
if missing:
    sys.exit("missing provenance labels: " + ", ".join(missing))

print(" ".join(f"{name}={labels[name]}" for name in required))
') || hotfix_die "$image carries no usable CI provenance.
   ci.pipeline-id and org.opencontainers.image.revision are both required.
   This looks like a locally built image. Build it in the pipeline (Chapter 23)
   and deploy the pipeline's artefact."

    printf 'hotfix: CI provenance ok for %s (%s)\n' "$image" "$provenance" >&2
}
