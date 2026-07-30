#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Applied during Ansible provisioning of cluster nodes
# Prevents any routing between blue and green pod CIDRs
#
# Without set -e and a read-back check, a rejected insert was persisted by
# iptables-save as though isolation had been applied: the rules file looked
# correct to the next audit while the two clusters could still route to each
# other. Verify each rule is actually present before saving, and never save a
# ruleset we have not confirmed.
set -euo pipefail

BLUE_POD_CIDR="${BLUE_POD_CIDR:-10.42.0.0/15}"
GREEN_POD_CIDR="${GREEN_POD_CIDR:-10.44.0.0/15}"
RULES_FILE="${IPTABLES_RULES_FILE:-/etc/iptables/rules.v4}"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ISOLATION] $*"; }
die() { log "ERROR: $*"; exit 1; }

command -v iptables      >/dev/null 2>&1 || die "iptables not found"
command -v iptables-save >/dev/null 2>&1 || die "iptables-save not found"

# Idempotent: -C tells us whether the rule is already there, so re-running the
# playbook does not stack duplicates.
ensure_drop() {
    local src="$1" dst="$2"

    if iptables -C FORWARD -s "$src" -d "$dst" -j DROP 2>/dev/null; then
        log "already present: FORWARD -s $src -d $dst -j DROP"
        return 0
    fi

    iptables -I FORWARD -s "$src" -d "$dst" -j DROP \
        || die "failed to insert DROP for $src -> $dst"

    # Read back. An insert that returned 0 but did not land (a conflicting
    # ruleset, a full table, a chain replaced underneath us) must not be treated
    # as success.
    iptables -C FORWARD -s "$src" -d "$dst" -j DROP 2>/dev/null \
        || die "inserted DROP for $src -> $dst but it is not present in FORWARD"

    log "inserted: FORWARD -s $src -d $dst -j DROP"
}

ensure_drop "$BLUE_POD_CIDR"  "$GREEN_POD_CIDR"
ensure_drop "$GREEN_POD_CIDR" "$BLUE_POD_CIDR"

# Only now is the running ruleset worth persisting.
mkdir -p "$(dirname "$RULES_FILE")"
tmp="$(mktemp "${RULES_FILE}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
iptables-save > "$tmp" || die "iptables-save failed; $RULES_FILE left unchanged"
grep -q -- "-d ${GREEN_POD_CIDR}" "$tmp" \
    || die "saved ruleset does not contain the blue->green DROP; $RULES_FILE left unchanged"
grep -q -- "-d ${BLUE_POD_CIDR}" "$tmp" \
    || die "saved ruleset does not contain the green->blue DROP; $RULES_FILE left unchanged"
mv "$tmp" "$RULES_FILE"
trap - EXIT

log "pod CIDR isolation verified and saved to $RULES_FILE"
