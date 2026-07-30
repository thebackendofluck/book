#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# mtu-proxmox-verify.sh — run ON the Proxmox host (secondary-host) before deploying VMs.
#
# Checks every layer of the physical → bridge → tap chain, detects the Linux
# bridge MTU inheritance trap, and emits actionable warnings with remediation
# commands. Exit code 0 = all good; non-zero = at least one warning.
#
# Usage:
#   bash proxmox/mtu-proxmox-verify.sh
#   BRIDGE=vmbr1 TARGET_MTU=9000 bash proxmox/mtu-proxmox-verify.sh
#
# Environment:
#   BRIDGE      — Linux bridge to check (default: vmbr0)
#   TARGET_MTU  — desired MTU (default: 9000)
#   VM_IPS      — space-separated list of VM IPs to ping-test (default: auto from bridge fdb)

set -euo pipefail

BRIDGE="${BRIDGE:-vmbr0}"
TARGET_MTU="${TARGET_MTU:-9000}"
VM_IPS="${VM_IPS:-}"

PING_PAYLOAD=$(( TARGET_MTU - 28 ))   # MTU - 20 (IP) - 8 (ICMP)
WARN=0

red()    { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
yellow() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
green()  { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
info()   { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }

warn() { yellow "$@"; WARN=$(( WARN + 1 )); }
fail() { red "$@";    WARN=$(( WARN + 1 )); }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Proxmox MTU verification — bridge=${BRIDGE} target=${TARGET_MTU}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Bridge MTU ────────────────────────────────────────────────────────────

bridge_mtu=$(cat "/sys/class/net/${BRIDGE}/mtu" 2>/dev/null || echo "NOT_FOUND")

if [[ "$bridge_mtu" == "NOT_FOUND" ]]; then
    fail "Bridge ${BRIDGE} not found. Check BRIDGE= env var."
elif [[ "$bridge_mtu" -lt "$TARGET_MTU" ]]; then
    fail "Bridge ${BRIDGE} MTU=${bridge_mtu} < ${TARGET_MTU}."
    echo "     → Fix: ip link set ${BRIDGE} mtu ${TARGET_MTU}"
    echo "       Persist: add 'mtu ${TARGET_MTU}' under 'iface ${BRIDGE}' in /etc/network/interfaces"
else
    green "Bridge ${BRIDGE} MTU=${bridge_mtu}"
fi

# ── 2. Physical NIC(s) attached to the bridge ────────────────────────────────

bridge_ports=$(ls "/sys/class/net/${BRIDGE}/brif/" 2>/dev/null || true)
if [[ -z "$bridge_ports" ]]; then
    warn "No ports found on bridge ${BRIDGE} — is it configured?"
else
    for port in $bridge_ports; do
        if [[ "$port" == tap* ]] || [[ "$port" == veth* ]]; then
            # tap/veth are VM virtual NICs — handled in section 3
            continue
        fi
        port_mtu=$(cat "/sys/class/net/${port}/mtu" 2>/dev/null || echo "?")
        if [[ "$port_mtu" == "?" ]]; then
            warn "Cannot read MTU for bridge port ${port}"
        elif [[ "$port_mtu" -lt "$TARGET_MTU" ]]; then
            fail "Physical NIC ${port} MTU=${port_mtu} < ${TARGET_MTU}."
            echo "     → Fix: ip link set ${port} mtu ${TARGET_MTU}"
            echo "       Persist: add 'mtu ${TARGET_MTU}' under 'iface ${port}' in /etc/network/interfaces"
        else
            green "Physical NIC ${port} MTU=${port_mtu}"
        fi
    done
fi

# ── 3. tap devices (VM NICs) — the Linux bridge MTU inheritance trap ─────────

tap_devices=$(find "/sys/class/net/${BRIDGE}/brif" -maxdepth 1 -name 'tap*' -printf '%f\n' 2>/dev/null || true)
min_tap_mtu=""

if [[ -z "$tap_devices" ]]; then
    info "No tap devices on ${BRIDGE} yet (no VMs running)."
else
    for tap in $tap_devices; do
        tap_mtu=$(cat "/sys/class/net/${tap}/mtu" 2>/dev/null || echo "?")
        if [[ "$tap_mtu" == "?" ]]; then
            warn "Cannot read MTU for tap device ${tap}"
            continue
        fi
        if [[ -z "$min_tap_mtu" ]] || [[ "$tap_mtu" -lt "$min_tap_mtu" ]]; then
            min_tap_mtu="$tap_mtu"
        fi
        if [[ "$tap_mtu" -lt "$TARGET_MTU" ]]; then
            # Identify which VM owns this tap
            vmid=$(echo "$tap" | grep -oP '(?<=tap)\d+' || echo "?")
            fail "tap ${tap} (VM ${vmid}) MTU=${tap_mtu} < ${TARGET_MTU}."
            echo "     → The Linux bridge will inherit this lower MTU!"
            echo "       Fix (persistent): add 'mtu=1' to net0/net1 in VM ${vmid} config:"
            echo "         qm set ${vmid} --net0 \$(qm config ${vmid} | grep '^net0' | cut -d' ' -f2),mtu=1"
            echo "       Then restart the VM so QEMU advertises MTU via VIRTIO_NET_F_MTU."
        else
            green "tap ${tap} MTU=${tap_mtu}"
        fi
    done

    if [[ -n "$min_tap_mtu" ]] && [[ "$min_tap_mtu" -lt "$TARGET_MTU" ]]; then
        actual_bridge=$(cat "/sys/class/net/${BRIDGE}/mtu")
        if [[ "$actual_bridge" -lt "$TARGET_MTU" ]]; then
            fail "Bridge ${BRIDGE} has dropped to MTU=${actual_bridge} due to low-MTU tap device."
            echo "     → Set 'mtu ${TARGET_MTU}' explicitly on the bridge stanza in"
            echo "       /etc/network/interfaces (explicit value overrides member minimum)."
        fi
    fi
fi

# ── 4. VLAN 802.1Q headroom check ───────────────────────────────────────────

vlan_ifaces=$(ip link show | grep -E '@' | awk '{print $2}' | tr -d ':' | grep "${BRIDGE}\." || true)
for viface in $vlan_ifaces; do
    vlan_mtu=$(cat "/sys/class/net/${viface}/mtu" 2>/dev/null || echo "?")
    if [[ "$vlan_mtu" != "?" ]] && [[ "$vlan_mtu" -gt "$(( TARGET_MTU - 4 ))" ]]; then
        warn "VLAN interface ${viface} MTU=${vlan_mtu}: VLAN 802.1Q adds 4 B — ensure"
        echo "     switch l2mtu ≥ $(( TARGET_MTU + 22 )) (frame = payload + Eth 14 + VLAN 4 + FCS 4)."
    fi
done

# ── 5. End-to-end ping test to known VM IPs ──────────────────────────────────

if [[ -z "$VM_IPS" ]]; then
    # Auto-detect from ARP cache (VMs that are up)
    VM_IPS=$(arp -n 2>/dev/null | awk '/192\.168\.90\./ {print $1}' | head -10 || true)
fi

if [[ -z "$VM_IPS" ]]; then
    info "No VM IPs to ping-test. Set VM_IPS='ip1 ip2 ...' to enable."
else
    echo ""
    info "Ping test with DF set (payload ${PING_PAYLOAD} B = MTU ${TARGET_MTU} - 28):"
    for ip in $VM_IPS; do
        if ping -M "do" -c 2 -W 2 -s "$PING_PAYLOAD" "$ip" >/dev/null 2>&1; then
            green "  Ping OK → ${ip} (MTU ${TARGET_MTU} end-to-end)"
        else
            fail "  Ping FAILED → ${ip} at MTU ${TARGET_MTU}."
            echo "       Possible: bridge clamped, tap MTU low, VM kernel < 4.14, or switch limit."
            echo "       Debug: ping -M do -s $(( TARGET_MTU - 100 - 28 )) ${ip}  (try smaller size)"
        fi
    done
fi

# ── 6. Summary ───────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "$WARN" -eq 0 ]]; then
    green "All checks passed — MTU ${TARGET_MTU} end-to-end on bridge ${BRIDGE}."
    echo "  Proceed with VM provisioning."
else
    fail "${WARN} issue(s) found — resolve before deploying postgres-aegis cluster."
    echo "  Re-run after fixes: bash proxmox/mtu-proxmox-verify.sh"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit "$WARN"
