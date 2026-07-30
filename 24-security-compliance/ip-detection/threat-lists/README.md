# IP Threat Intelligence for iGaming — Consolidation Toolkit

This directory contains a production-ready script that downloads, validates, deduplicates, and exports IP threat lists for iGaming fraud detection, geo-restriction enforcement, and regulatory compliance.

## Why this matters for iGaming

Regulators in the UK (UKGC), Malta (MGA), and across regulated markets require operators to:

- Block players connecting through anonymisation services (Tor, commercial VPNs)
- Prevent multi-accounting and bonus abuse through open proxies
- Detect bot activity and scripted players
- Verify geographic access restrictions are not being circumvented

A consolidated threat intelligence feed, refreshed daily, is the foundation of a compliant and fraud-resistant iGaming platform.

---

## Files

| File | Purpose |
|------|---------|
| `consolidate-lists.py` | Main script: downloads, parses, deduplicates, and exports |
| `sources.json` | Full source catalog with URLs, formats, reliability ratings |
| `output/` | Generated output files (created on first run) |
| `cache/` | Raw downloaded files (created on first run) |

---

## Quick Start

```bash
# Install no extra dependencies — uses Python 3 stdlib only
python3 --version  # Requires Python 3.7+

# Run with defaults (downloads all sources, writes to ./output)
python3 consolidate-lists.py

# Verbose output
python3 consolidate-lists.py --verbose

# Custom output and cache directories
python3 consolidate-lists.py \
    --output-dir /var/lib/threat-intel/output \
    --cache-dir /var/lib/threat-intel/cache

# Offline mode (use cached data, no network calls)
python3 consolidate-lists.py --no-download

# Adjust IPsum sensitivity (default 3, range 1-11)
# Higher = fewer IPs but higher confidence malicious
python3 consolidate-lists.py --min-ipsum-score 5

# Full help
python3 consolidate-lists.py --help
```

---

## Output Files

After a successful run, the `output/` directory contains:

### Plain text lists (one entry per line)

| File | Contents | Format |
|------|----------|--------|
| `tor-exits.txt` | Tor exit node IPv4 addresses | Plain IP |
| `vpn-ips.txt` | Commercial VPN provider ranges | CIDR |
| `proxy-ips.txt` | Open HTTP/SOCKS proxy IPs | Plain IP |
| `datacenter-ranges.txt` | Cloud/hosting provider ranges | CIDR |
| `bot-ips.txt` | Known bot and abuse automation IPs | Plain IP |
| `abuse-ips.txt` | Malicious IPs and ranges | IP or CIDR |
| `consolidated-threats.txt` | All categories merged with labels | `<ip_or_cidr> <category>` |

### Import-ready export formats

| File | Purpose |
|------|---------|
| `redis-load.sh` | Shell script for bulk Redis import via `redis-cli` |
| `cloudflare-kv-bulk.json` | Cloudflare Workers KV bulk upload (Wrangler format) |
| `aws-waf-ipset.json` | AWS WAF IP set (combined, IPv4) |
| `aws-waf-<category>-ipv4.json` | Per-category AWS WAF IP sets |
| `aws-waf-<category>-ipv6.json` | Per-category AWS WAF IP sets (IPv6) |
| `run-stats.json` | Run statistics: counts, per-source breakdown, timing |

---

## Cron Setup (Daily Refresh)

```cron
# Run daily at 03:00 UTC
0 3 * * * /usr/bin/python3 /opt/threat-intel/consolidate-lists.py \
    --output-dir /opt/threat-intel/output \
    --cache-dir /opt/threat-intel/cache \
    >> /var/log/threat-intel-consolidate.log 2>&1
```

The cache TTL defaults to 24 hours. Running the script more frequently than the TTL will use cached data, making subsequent runs instant.

---

## Sources

### Tor Exit Nodes

| Source ID | URL | Reliability | Notes |
|-----------|-----|-------------|-------|
| `tor_project_bulk` | https://check.torproject.org/torbulkexitlist | Authoritative | Official Tor Project list. Updated hourly. |
| `firehol_tor_exits` | https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/tor_exits.ipset | Very high | Mirrors TorDNSEL. Aggregated by FireHOL. |
| `firehol_tor_exits_7d` | .../tor_exits_7d.ipset | Very high | 7-day historical window — broader coverage. |
| `et_tor` | .../et_tor.ipset | High | Emerging Threats Tor list. |
| `dan_me_uk_tor` | .../dm_tor.ipset | High | Dynamic Tor node list including non-exits. |
| `secops_tor` | SecOps-Institute/Tor-IP-Addresses | Medium | Community-maintained supplemental list. |

**iGaming recommendation:** Use the Tor Project bulk list as primary, FireHOL 7-day as secondary for recently-exited nodes.

### VPN Provider IP Ranges

| Source ID | URL | Reliability | Notes |
|-----------|-----|-------------|-------|
| `x4bnet_vpn_ipv4` | X4BNet/lists_vpn output/vpn/ipv4.txt | Very high | Curated VPN provider CIDR ranges (IPv4). Best available public source. |
| `x4bnet_vpn_ipv6` | X4BNet/lists_vpn output/vpn/ipv6.txt | Very high | IPv6 equivalent. |

**iGaming recommendation:** X4BNet is the best publicly available VPN IP range database. For production use, supplement with commercial providers (IPQualityScore, IPinfo, MaxMind).

### Datacenter / Hosting Provider Ranges

| Source ID | URL | Reliability | Notes |
|-----------|-----|-------------|-------|
| `x4bnet_datacenter_ipv4` | X4BNet/lists_vpn output/datacenter/ipv4.txt | Very high | Cloud/hosting CIDR ranges. |
| `jhassine_datacenters` | jhassine/server-ip-addresses datacenters.csv | High | Named datacenter ranges with vendor attribution (AWS, Azure, GCP, etc.). |

**iGaming recommendation:** Datacenter IPs should not be hard-blocked — legitimate corporate users behind VPNs will appear here. Use as a risk signal (+score) rather than a block rule.

### Open Proxy Lists

| Source ID | Maintainer | Type | Update Frequency |
|-----------|-----------|------|-----------------|
| `mmpx12_http` | mmpx12/proxy-list | HTTP | Hourly |
| `mmpx12_socks4` | mmpx12/proxy-list | SOCKS4 | Hourly |
| `mmpx12_socks5` | mmpx12/proxy-list | SOCKS5 | Hourly |
| `thespeedx_http` | TheSpeedX/PROXY-List | HTTP | Daily |
| `thespeedx_socks4` | TheSpeedX/PROXY-List | SOCKS4 | Daily |
| `thespeedx_socks5` | TheSpeedX/PROXY-List | SOCKS5 | Daily |
| `shiftytr_proxy` | ShiftyTR/Proxy-List | Mixed | Daily |
| `shiftytr_socks5` | ShiftyTR/Proxy-List | SOCKS5 | Daily |
| `hookzof_socks5` | hookzof/socks5_list | SOCKS5 | Daily |
| `monosans_http` | monosans/proxy-list | HTTP | Daily |
| `clarketm_proxy` | clarketm/proxy-list | Mixed | Weekly |
| `roosterkid_https` | roosterkid/openproxylist | HTTPS | Daily |
| `proxifly_all` | proxifly/free-proxy-list | All types | Daily |
| `firehol_socks_proxy` | FireHOL/blocklist-ipsets | SOCKS | Daily |
| `firehol_socks_proxy_7d` | FireHOL/blocklist-ipsets | SOCKS (7d) | Daily |
| `firehol_socks_proxy_30d` | FireHOL/blocklist-ipsets | SOCKS (30d) | Daily |

**iGaming recommendation:** Proxy lists have high churn — IPs appear and disappear within hours. Daily refresh is the minimum viable cadence. Combine with real-time proxy detection APIs for critical flows (registration, withdrawal).

### Bot IP Lists

| Source ID | Source | Notes |
|-----------|--------|-------|
| `firehol_botvrij_src` | botvrij.eu via FireHOL | Malicious source IPs from IOC feeds |
| `firehol_blocklist_de_bots` | blocklist.de via FireHOL | Forum/wiki registration bots, IRC bots |
| `firehol_botscout_30d` | BotScout via FireHOL | 30-day history of automated registration abuse |

**iGaming recommendation:** These lists are directly relevant to bonus abuse and account fraud. IPs from bot lists registering accounts should trigger KYC escalation.

### Abuse / Malicious IP Lists

| Source ID | Source | Reliability | Notes |
|-----------|--------|-------------|-------|
| `stamparm_ipsum` | stamparm/ipsum | Very high | Aggregates 80+ threat feeds. Score >= 3 recommended for iGaming. |
| `stamparm_blackbook` | stamparm/blackbook | High | Known malicious IPs with hostname context. |
| `firehol_level1` | FireHOL | Very high | Maximum protection, minimum false positives. Production-safe for blocking. |
| `firehol_level2` | FireHOL | High | Broader coverage. Use for risk scoring. |
| `firehol_abusers_1d` | FireHOL | High | Active abusers (last 24 hours). |
| `firehol_abusers_30d` | FireHOL | High | Persistent abusers (30 days). |
| `feodo_tracker` | abuse.ch | Very high | Botnet C2 IPs. Authoritative. |
| `dshield_blocklist` | SANS/DShield | Very high | Top 20 attacking /24 subnets. Updated every 15 minutes. |
| `spamhaus_drop` | Spamhaus | Very high | Hijacked/unallocated space used for attacks. |
| `spamhaus_edrop` | Spamhaus | Very high | Extended DROP list. Criminal-controlled IP space. |

---

## Integration Examples

### Redis Lookup (Node.js)

```javascript
const redis = require('ioredis');
const client = new redis();

async function getIpThreatCategory(ip) {
  // O(1) lookup for individual IPs
  const category = await client.get(`igaming:ip:${ip}`);
  if (category) return category;

  // Set membership check for CIDR ranges requires range iteration
  // For production, use a proper CIDR matching library or
  // pre-expand ranges into individual IPs during import
  const inTor = await client.sismember('igaming:threats:tor', ip);
  if (inTor) return 'tor';

  return null;
}
```

### Cloudflare Workers KV Import

```bash
# Import using Wrangler CLI
wrangler kv:bulk put --binding THREAT_IPS output/cloudflare-kv-bulk.json

# If batched (> 10,000 entries):
for f in output/cloudflare-kv-bulk-batch*.json; do
  wrangler kv:bulk put --binding THREAT_IPS "$f"
done
```

### Cloudflare Workers Lookup

```javascript
export default {
  async fetch(request, env) {
    const ip = request.headers.get('CF-Connecting-IP');
    const threatData = await env.THREAT_IPS.get(ip);

    if (threatData) {
      const threat = JSON.parse(threatData);
      if (threat.categories.includes('tor')) {
        return new Response('Access denied: Tor exit nodes are not permitted.', {
          status: 403,
        });
      }
      // Add risk score to downstream request headers
      request = new Request(request, {
        headers: {
          ...Object.fromEntries(request.headers),
          'X-Threat-Category': threat.primary,
          'X-Threat-Score': '1',
        },
      });
    }

    return fetch(request);
  },
};
```

### AWS WAF Import

```bash
# Create an IP set (first run)
aws wafv2 create-ip-set \
  --name "igaming-threats-tor-ipv4" \
  --scope REGIONAL \
  --ip-address-version IPV4 \
  --addresses file://output/aws-waf-tor-ipv4.json \
  --region eu-west-1

# Update an existing IP set
aws wafv2 update-ip-set \
  --name "igaming-threats-tor-ipv4" \
  --scope REGIONAL \
  --id <ip-set-id> \
  --lock-token <lock-token> \
  --addresses file://output/aws-waf-tor-ipv4.json \
  --region eu-west-1
```

### Nginx Geo Blocking

```nginx
# Generate nginx geo block from tor-exits.txt
# geo $is_tor {
#   default 0;
#   include /etc/nginx/conf.d/tor-exits.conf;
# }

# Convert tor-exits.txt to nginx format:
# awk '!/^#/ && NF {print $1 " 1;"}' tor-exits.txt > /etc/nginx/conf.d/tor-exits.conf
```

---

## IPsum Score Guide

The `stamparm/ipsum` source assigns each IP a score = number of threat intel blocklists that flagged it:

| Score | Confidence | Recommended Action |
|-------|-----------|-------------------|
| 1-2 | Low | Log only, monitor |
| 3-4 | Medium | Increase risk score, require additional verification |
| 5-7 | High | Block registration, flag for manual review |
| 8+ | Critical | Hard block, alert security team |

Default `--min-ipsum-score 3` balances coverage against false positives for iGaming use cases.

---

## Data Quality and False Positives

Open proxy lists and community threat feeds have inherent limitations:

1. **High churn**: Proxy IPs change frequently. Stale lists flag IPs reassigned to legitimate users.
2. **False positives on corporate VPNs**: Many corporate IPs appear in datacenter ranges.
3. **IPv6 coverage gap**: Most lists focus on IPv4. IPv6 VPN/proxy detection requires commercial APIs.

**Recommended production architecture:**

1. Use these open lists for initial risk scoring (not hard blocking for most categories)
2. Hard block only: Tor exits (regulatory requirement), Spamhaus DROP/eDROP (very low false positive rate), Feodo Tracker C2 IPs
3. Use risk scores to trigger step-up verification (SMS OTP, enhanced KYC) rather than outright blocks for VPN/proxy/datacenter hits
4. Supplement with commercial real-time APIs (IPQualityScore, IPinfo, MaxMind GeoIP2) for production traffic

---

## License Notes

Each source carries its own license. Key constraints:

- **BotScout** (botscout_30d): Non-commercial use only
- **DShield** (dshield_blocklist): CC BY-NC-SA 2.5 (non-commercial)
- **Spamhaus**: Free for non-commercial use; commercial license required for high-volume production use
- **FireHOL aggregated lists**: CC BY-SA 4.0
- **All GitHub proxy lists**: MIT or equivalent permissive

For commercial iGaming deployments, review the Spamhaus and DShield terms. Commercial licenses are available and are standard practice for regulated operators.

---

## Operational Runbook

### Daily Operations

```bash
# Check last run stats
cat output/run-stats.json | python3 -m json.tool | grep -E "generated|total_unique|elapsed"

# Re-run with fresh data
python3 consolidate-lists.py --verbose

# Check for sources that failed
cat output/run-stats.json | python3 -c "
import json, sys
s = json.load(sys.stdin)
failed = [x for x in s['sources'] if x['error']]
for f in failed:
    print(f['id'], '->', f['error'])
"
```

### Troubleshooting

**All sources returning empty:** Check network connectivity. Run without `--no-download`.

**Specific source failing:** Check the URL in `sources.json`. GitHub raw URLs may change if a repository is renamed. The `cache/` directory still has the last successful download.

**Output files growing too large for AWS WAF:** AWS WAF has a hard 10,000 address limit per IP set. The script automatically truncates and notes this in `_metadata.truncated`. Consider splitting by risk priority: use FireHOL Level 1 + Tor exits for hard blocking, remaining lists for risk scoring only.

**Redis import taking too long:** Use `--pipe` mode:
```bash
bash output/redis-load.sh | redis-cli --pipe
```
Or use the Redis LOAD script directly with pipe mode for sets of millions of entries.
