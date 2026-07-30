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

# Read-only Linux and load-balancer preflight for high-RPS event planning.

set -euo pipefail

TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '1,80p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

read_sysctl() {
  local key="$1"
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n "$key" 2>/dev/null || true
  fi
}

print_param() {
  local key="$1"
  local value
  value="$(read_sysctl "$key")"
  printf "%-45s %s\n" "$key" "${value:-unavailable}"
}

echo "# Linux/LB preflight"
echo
echo "## Kernel queues and socket limits"
print_param net.core.somaxconn
print_param net.ipv4.tcp_max_syn_backlog
print_param net.core.netdev_max_backlog
print_param net.ipv4.ip_local_port_range
print_param net.ipv4.tcp_fin_timeout
print_param net.ipv4.tcp_tw_reuse
print_param net.netfilter.nf_conntrack_max
print_param fs.file-max
print_param fs.nr_open

echo
echo "## File descriptors"
printf "%-45s %s\n" "ulimit soft nofile" "$(ulimit -Sn)"
printf "%-45s %s\n" "ulimit hard nofile" "$(ulimit -Hn)"
if [[ -r /proc/sys/fs/file-nr ]]; then
  printf "%-45s %s\n" "/proc/sys/fs/file-nr" "$(cat /proc/sys/fs/file-nr)"
fi

echo
echo "## Conntrack"
if [[ -r /proc/sys/net/netfilter/nf_conntrack_count ]]; then
  count="$(cat /proc/sys/net/netfilter/nf_conntrack_count)"
  max="$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)"
  printf "%-45s %s / %s\n" "nf_conntrack_count/max" "$count" "$max"
else
  echo "nf_conntrack_count unavailable"
fi

echo
echo "## Interfaces"
if command -v ip >/dev/null 2>&1; then
  ip -o link show | awk -F': ' '{print $2}' | while read -r iface; do
    [[ "$iface" == "lo" ]] && continue
    mtu="$(ip link show "$iface" | awk '/mtu/{for(i=1;i<=NF;i++) if ($i=="mtu") print $(i+1)}')"
    state="$(ip link show "$iface" | awk '/state/{for(i=1;i<=NF;i++) if ($i=="state") print $(i+1)}')"
    printf "%-20s mtu=%-6s state=%s\n" "$iface" "${mtu:-?}" "${state:-?}"
  done
else
  echo "ip command unavailable"
fi

echo
echo "## Softnet drops"
if [[ -r /proc/net/softnet_stat ]]; then
  awk '{drops += strtonum("0x"$2)} END {print "softnet_drops_total " drops}' /proc/net/softnet_stat 2>/dev/null || \
    awk '{print $2}' /proc/net/softnet_stat | head -5
else
  echo "/proc/net/softnet_stat unavailable"
fi

if [[ -n "$TARGET" ]]; then
  echo
  echo "## Target checks: $TARGET"
  if command -v ping >/dev/null 2>&1; then
    ping -c 3 -W 2 "$TARGET" || true
    echo
    echo "MTU probe payload 1472, do-not-fragment:"
    ping -M "do" -s 1472 -c 1 -W 2 "$TARGET" || true
  else
    echo "ping command unavailable"
  fi
fi

echo
echo "## Interpretation"
echo "- somaxconn and tcp_max_syn_backlog should be high enough for surge handshakes."
echo "- conntrack should stay below 60-70% during planned peaks."
echo "- MTU must be validated end to end before enabling jumbo frames."
echo "- This script does not apply changes."
