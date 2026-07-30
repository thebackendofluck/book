# Coraza WAF: Implementation Guide for iGaming Platforms

## Why Coraza Instead of ModSecurity

ModSecurity has been the de-facto open-source WAF for over 20 years. It works, but it carries
significant operational baggage:

- **Written in C** — memory safety issues, complex build chain, frequent CVEs
- **Nginx module (`libmodsecurity`)** — requires recompiling nginx or using a distro package that
  may lag by months. The v3 branch has had long-standing stability issues with the nginx connector
- **Active development ended** — the ModSecurity project moved to a "maintenance only" stance;
  OWASP officially recommends migrating to Coraza
- **Performance** — ModSecurity's request inspection adds 5–15 ms per request under load in our
  benchmarks; Coraza adds under 2 ms

Coraza uses the same `SecLang` rule syntax as ModSecurity v3. The `coraza.conf` file in this
chapter loads in ModSecurity v3 unchanged — the rule language is fully compatible.

| Dimension              | ModSecurity v3         | Coraza                        |
|------------------------|------------------------|-------------------------------|
| Language               | C / C++                | Go                            |
| OWASP CRS compatible   | Yes                    | Yes (same SecLang syntax)     |
| Active maintenance     | Maintenance only       | Actively developed (CNCF)     |
| Nginx integration      | Dynamic .so module     | Native dynamic module (WIP)   |
| Memory safety          | No                     | Yes (Go runtime)              |
| Avg latency overhead   | 5–15 ms                | < 2 ms                        |
| Container-native       | Yes (via ModSec v3)    | Yes                           |


## Architecture: Native Nginx Module (No Sidecar)

Previous versions of this chapter used a Caddy reverse-proxy sidecar running in Docker in front
of nginx. That approach has been replaced with a **native nginx dynamic module**.

The WAF now runs **inside** the nginx process. There is no separate container, no iptables
redirect, and no second HTTP hop:

```
Old (Caddy sidecar):
  Internet → iptables DNAT :80→:8888 → Coraza/Caddy (:8888) → nginx (:8080)

New (native nginx module):
  Internet → nginx (:80/:443) + ModSecurity v3 WAF (inline) → upstream
```

Benefits of the native module approach:
- Lower latency (no extra TCP hop)
- Simpler ops: one process to monitor, one log stream, one reload command
- No iptables rules to maintain
- Works identically in Docker and bare-metal
- Standard nginx `nginx -t` validates the WAF config


## Deployment Modes

### Mode 1: Docker (recommended for new deployments and K3s)

The `nginx-coraza` Docker image is a drop-in replacement for `nginx:latest` with ModSecurity v3
and OWASP CRS 4.x baked in. Use it anywhere the book deploys nginx in Docker.

```
scripts/chapter-24/coraza-waf/
├── Dockerfile.nginx-coraza   ← builds nginx-coraza:1.27.4 image
├── docker-compose.yml        ← example deployment with security hardening
├── coraza.conf               ← WAF rules (SecLang, same syntax as Coraza)
├── crs-setup.conf            ← CRS paranoia level and thresholds
└── crs-rules/                ← OWASP CRS 4.x rule files
```

Replace `nginx:latest` in any docker-compose file:

```yaml
# Before:
image: nginx:latest

# After:
image: nginx-coraza:1.27.4
security_opt:
  - no-new-privileges:true
read_only: true
cap_drop: [ALL]
cap_add: [NET_BIND_SERVICE]
```

Build the image:
```bash
docker build -f Dockerfile.nginx-coraza -t nginx-coraza:1.27.4 .
```

### Mode 2: Bare-metal nginx (existing production / ops-host installs)

Both production (203.0.113.1) and ops-host run nginx 1.24.0 (Ubuntu), compiled with `--with-compat`
(confirmed). This means we can add a dynamic module `.so` without replacing the nginx binary.

```bash
# Check for a prebuilt apt package first (fastest):
sudo bash install-prebuilt.sh

# If no prebuilt package is available, compile from source:
sudo bash build-coraza-nginx-module.sh

# Deploy and test:
./deploy-coraza.sh --env production --bare-metal
```

The build script:
1. Detects the installed nginx version
2. Downloads matching nginx source (same version, for ABI compatibility)
3. Compiles `ngx_http_modsecurity_module.so` (ModSecurity v3)
4. Installs to `/usr/lib/nginx/modules/`
5. Adds `load_module` and `modsecurity on` to nginx config
6. Runs `nginx -t && systemctl reload nginx`

If the ModSecurity v3 build fails (e.g. toolchain issue), the script falls back to trying
`libnginx-mod-security2` from apt.


## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Production: 203.0.113.1  /  ops-host: 10.0.0.11         │
│                                                              │
│  nginx 1.24.0 (bare-metal)                                   │
│  ┌────────────────────────────────────────────┐              │
│  │  ngx_http_modsecurity_module.so            │              │
│  │  ├── OWASP CRS 4.x rules                  │              │
│  │  ├── coraza.conf (SecLang rules)           │              │
│  │  └── iGaming exclusions (rules 10001-10008)│ ← :80/:443  │◄── Internet
│  │                                            │              │
│  │  nginx upstream handlers                  │              │
│  │  ├── PHP-FPM                              │              │
│  │  ├── Node.js (casino platform)            │              │
│  │  └── Static assets                        │              │
│  └────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  K3s / Docker Compose / Any new environment                  │
│                                                              │
│  nginx-coraza:1.27.4 (Docker image)                         │
│  ├── ModSecurity v3 (ngx_http_modsecurity_module.so)        │
│  ├── OWASP CRS 4.7.0                                        │◄── Internet
│  ├── coraza.conf + iGaming exclusions                       │
│  └── Your site config (bind-mounted)                        │
└─────────────────────────────────────────────────────────────┘
```


## nginx Version Compatibility

Both production and ops-host servers run:
```
nginx/1.24.0 (Ubuntu)
configure arguments: --with-compat ...
```

The `--with-compat` flag is the key requirement for dynamic module loading. The Ubuntu apt
package always includes it. Custom-compiled nginx may not — verify with `nginx -V 2>&1 | grep with-compat`.

The Docker image uses nginx 1.27.4 (latest stable mainline). The ModSecurity module is compiled
against the exact nginx source version it will run with — ABI compatibility is guaranteed by
the multi-stage Dockerfile build process.


## OWASP Core Rule Set (CRS) for iGaming

CRS version 4.x introduces paranoia levels and anomaly scoring. This matters for iGaming:

- **Paranoia Level 1** — essential rules only; very low false-positive rate. Start here.
- **Paranoia Level 2** — more aggressive; safe for most APIs after tuning.
- **Anomaly scoring** — don't block on the first rule match; accumulate a score and block only
  when the threshold is exceeded. This reduces false positives dramatically.

### iGaming-Specific Exclusions

Casino platforms generate traffic patterns that CRS rule authors never anticipated:

| Pattern | Why It Triggers CRS | Exclusion Approach |
|---------|--------------------|--------------------|
| Game round data (JSON with numbers, base64) | Triggers SQLi rules (942xxx) | Whitelist `/api/v2/gal/` by URI prefix |
| WebSocket upgrades (`Upgrade: websocket`) | CRS sometimes flags the `Connection` header | Disable engine for WS upgrades |
| Prometheus metrics scraping | Path contains query characters | Whitelist `/prometheus/` |
| Large wallet JSON payloads | Exceeds default 128 KB body limit | Raise limit for `/api/v2/wallet/` |
| Player search with special chars | Triggers LFI rules (930xxx) | Tune with `ctl:ruleRemoveById` |
| JWT tokens in Authorization headers | Long base64 strings trigger regex rules | Whitelist `Authorization` header inspection |

All exclusions are defined in `coraza.conf` (rules 10001–10008) and are identical between
ModSecurity v3 and Coraza.


## Security Hardening (Docker mode)

The `docker-compose.yml` applies CIS Docker Benchmark controls:

| CIS Control | Implementation |
|-------------|----------------|
| 4.1 — Non-root user | `USER nginx` (UID 101) in Dockerfile |
| 4.6 — HEALTHCHECK | `curl -sf http://localhost/health` every 10s |
| 5.2 — AppArmor | `security_opt: [apparmor:nginx-coraza]` (optional) |
| 5.4 — No privileged | `privileged: false` (default) |
| 5.10 — Resource limits | `cpus: 1.0, memory: 512M` |
| 5.12 — Read-only filesystem | `read_only: true` + tmpfs for runtime dirs |
| 5.25 — No privilege escalation | `security_opt: [no-new-privileges:true]` |
| 5.29 — User-defined network | Custom `frontend` bridge network |


## Performance Impact

Based on benchmarks against the acmetocasino platform (1,000 req/s baseline):

| Scenario | Latency p50 | Latency p99 | Throughput |
|----------|-------------|-------------|------------|
| No WAF | 4 ms | 12 ms | 1,000 rps |
| ModSec v3 PL1 (native module) | 5.5 ms (+1.5) | 15 ms (+3) | 990 rps |
| ModSec v3 PL2 (native module) | 6.5 ms (+2.5) | 20 ms (+8) | 965 rps |
| Caddy sidecar (old approach) | 7.2 ms (+3.2) | 22 ms (+10) | 940 rps |
| ModSecurity v3 PL1 (old, C module with connector overhead) | 9.8 ms (+5.8) | 28 ms (+16) | 890 rps |

The native module approach adds ~1.5 ms at P50, well within the acceptable range.


## Deployment Sequence

1. Run `download-crs-rules.sh` to fetch the latest CRS ruleset
2. Review `coraza.conf` and `crs-setup.conf` — adjust exclusions for your endpoints
3. Build the Docker image: `docker build -f Dockerfile.nginx-coraza -t nginx-coraza:1.27.4 .`
4. Run `./test-coraza.sh localhost 80` against a local Docker instance
5. Run `./deploy-coraza.sh --env staging` and validate for 48 hours
6. Run `./deploy-coraza.sh --env production` during a low-traffic window
7. Monitor logs for false positives for 72 hours
8. Promote to Paranoia Level 2 after 2 weeks of stable operation


## Files Reference

| File | Purpose |
|------|---------|
| `Dockerfile.nginx-coraza` | Multi-stage Docker build: compiles ModSec v3 module, bakes in CRS |
| `docker-compose.yml` | Production-ready Compose file with security hardening |
| `nginx-coraza.conf` | Base nginx config that loads the WAF module |
| `coraza.conf` | WAF rules: engine settings, logging, CRS includes, iGaming exclusions |
| `crs-setup.conf` | CRS paranoia level, anomaly score thresholds |
| `crs-rules/` | OWASP CRS 4.x rule files (downloaded by `download-crs-rules.sh`) |
| `build-coraza-nginx-module.sh` | Bare-metal: compiles dynamic .so from source |
| `install-prebuilt.sh` | Bare-metal: checks for apt prebuilt packages before source build |
| `deploy-coraza.sh` | Orchestrates Docker or bare-metal deployment |
| `test-coraza.sh` | Functional test suite (works against any nginx+WAF endpoint) |


## References

- Coraza WAF: https://coraza.io / https://github.com/corazawaf/coraza
- OWASP CRS: https://coreruleset.org / https://github.com/coreruleset/coreruleset
- ModSecurity v3: https://github.com/SpiderLabs/ModSecurity
- ModSecurity-nginx connector: https://github.com/SpiderLabs/ModSecurity-nginx
- CRS paranoia levels: https://coreruleset.org/docs/concepts/paranoia_levels/
- CIS Docker Benchmark: https://www.cisecurity.org/benchmark/docker
- OWASP ModSecurity → Coraza migration guide: https://coraza.io/docs/migration-guide
