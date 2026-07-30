K3s Cluster Performance Optimization Report
============================================
Date: 2026-04-04
Cluster: K3s v1.35.3 on ops-host (128 CPU cores, ~480GB RAM)
Namespace: casino-prod
Stack: 12 Nginx TLS -> 6 Varnish -> 10 Casino Service (FastAPI)

================================================================================
BASELINE MEASUREMENTS
================================================================================

wrk (4 threads, 15s):
  100 connections:  ~38,000 req/s   | avg latency 20.33ms
  500 connections:  ~96,143 req/s   | avg latency 23.57ms

curl single request: ~5ms total (3.5-5ms TLS handshake)
DNS: 100 lookups in <1 second (already fast)
Node: CPU 9%, Memory 44%
Conntrack: 6,809 / 4,194,304 (0.16%)

================================================================================
OPTIMIZATION 1: CoreDNS Tuning
================================================================================

Changes applied to ConfigMap coredns (kube-system):
  - Cache TTL: 30s -> 300s (success), 30s denial cache
  - Cache size: default -> 9984 entries for both success and denial
  - Added bufsize 1232 for EDNS compliance
  - Added forward max_concurrent 2000 for upstream DNS
  - REMOVED autopath (tested, caused performance regression)

Before: 100 lookups <1s
After:  200 lookups <1s (no measurable difference for synthetic test)

Verdict: KEPT
  - Cache increase helps under sustained load with many unique pods
  - EDNS bufsize prevents fragmentation issues
  - max_concurrent prevents upstream DNS bottleneck under spike
  - Minimal impact on synthetic benchmarks but protects against DNS storms

================================================================================
OPTIMIZATION 2: Kernel Tuning (Host Level)
================================================================================

Status: ALREADY OPTIMIZED - No changes needed

Current values (all meet or exceed targets):
  net.core.somaxconn = 65535           (target: 65535)
  net.ipv4.tcp_max_syn_backlog = 65535 (target: 65535)
  net.core.netdev_max_backlog = 65535  (target: 65535)
  net.ipv4.ip_local_port_range = 1024-65535 (target: 1024-65535)
  net.ipv4.tcp_tw_reuse = 1           (target: 1)
  net.ipv4.tcp_fin_timeout = 10       (was 15, reduced to 10)
  net.core.rmem_max = 268435456       (target: 16777216, EXCEEDS 16x)
  net.core.wmem_max = 268435456       (target: 16777216, EXCEEDS 16x)
  net.ipv4.tcp_rmem = 4096 87380 134217728 (target max: 16M, EXCEEDS 8x)
  net.ipv4.tcp_wmem = 4096 65536 134217728 (target max: 16M, EXCEEDS 8x)
  fs.file-max = 2097152               (target: 2097152)
  net.netfilter.nf_conntrack_max = 4194304 (target: 1048576, EXCEEDS 4x)

Only change: tcp_fin_timeout 15 -> 10 (reclaims orphaned FIN_WAIT_2 sockets 5s faster)

Verdict: KEPT (tcp_fin_timeout=10)
  Make persistent: add to /etc/sysctl.d/99-k3s-tuning.conf

================================================================================
OPTIMIZATION 3: K3s / Kubelet Tuning
================================================================================

Created /etc/rancher/k3s/config.yaml:
  kube-apiserver-arg:
    - "max-requests-inflight=400"      (default: 40)
    - "max-mutating-requests-inflight=200" (default: 20)
  kubelet-arg:
    - "max-pods=250"                   (default: 110)
    - "serialize-image-pulls=false"    (parallel image pulls)
    - "event-qps=0"                    (unlimited events)
    - "kube-api-qps=100"              (default: 50)
    - "kube-api-burst=200"            (default: 100)

Status: CONFIG WRITTEN, NOT YET APPLIED (requires k3s restart)

Verdict: RECOMMENDED
  Apply during next maintenance window:
    sudo systemctl restart k3s

================================================================================
OPTIMIZATION 4: Traefik Ingress
================================================================================

Status: NOT IN HOT PATH - No changes needed

Analysis:
  - nginx-tls is exposed as NodePort (30443) directly
  - Traffic path: Client -> NodePort:30443 -> nginx-tls -> varnish -> casino-service
  - Traefik handles ports 80/443 via LoadBalancer but is NOT in casino-prod path
  - No Ingress resources defined for casino-prod

Verdict: NO ACTION NEEDED

================================================================================
OPTIMIZATION 5: Nginx TLS Pod Tuning
================================================================================

Changes applied to ConfigMap nginx-config (casino-prod):
  - Added reset_timedout_connection on (reclaims dead connections faster)
  - Added proxy_buffering on with explicit buffer sizes:
      proxy_buffer_size 8k
      proxy_buffers 16 8k
      proxy_busy_buffers_size 16k
  - Increased SSL session cache: 10m -> 50m
    (supports ~200K cached TLS sessions, reduces TLS handshake overhead)

Before (100c): ~38,000 req/s
After  (100c): ~112,000 req/s (+194%)

Before (500c): ~96,143 req/s
After  (500c): ~105,247 req/s (+9.5%)

Verdict: KEPT
  The SSL session cache increase is the likely driver of the 100c improvement,
  as returning connections can resume TLS sessions from cache instead of
  full handshakes.

================================================================================
OPTIMIZATION 6: Varnish Cache Tuning
================================================================================

Changes applied to ConfigMap varnish-vcl (casino-prod):
  - Grace period: 30s -> 5m (serves stale content during backend refresh)
  - Keep period: 60s -> 10m (keeps objects for conditional requests)
  - Static asset TTL: 1h -> 4h (JS, CSS, images)
  - 500-error grace: 30s -> 5m (critical: serves stale on backend errors)

Impact: Not directly measurable via wrk (root path returns 404 from backend,
not cacheable). Real impact is during traffic spikes and backend instability.

Verdict: KEPT
  Grace=5m means Varnish continues serving stale content for up to 5 minutes
  if the backend is slow or down. This is critical for iGaming uptime during
  casino-service scaling events.

================================================================================
OPTIMIZATION 7: Pod Resource / QoS Analysis
================================================================================

Current state:
  Casino Service: 4m/99Mi actual vs 100m/128Mi request (4% CPU utilization)
  Nginx TLS:      50m/450Mi actual vs 500m/512Mi request (10% CPU)
  Varnish:        50m/300Mi actual vs 200m/512Mi request (25% CPU)
  All pods: Burstable QoS

Recommendation (NOT applied - requires load testing under production traffic):
  1. Casino Service: Consider reducing min replicas from 10 to 5 at idle
     (HPA at 3% CPU, 10 pods are massively overprovisioned at current load)
  2. For Guaranteed QoS on critical nginx-tls pods:
     Set requests=limits (e.g., cpu=1, memory=1Gi for both)
     This prevents CPU throttling and ensures priority scheduling

Status: NOT APPLIED (risk vs reward unfavorable without production traffic data)

================================================================================
OPTIMIZATION 8: Network Policies
================================================================================

Status: NO NETWORK POLICIES FOUND

No NetworkPolicies exist in casino-prod namespace. This means no additional
packet filtering overhead from Kubernetes NetworkPolicy enforcement.

Note: Consider adding NetworkPolicies for security (restrict inter-namespace
traffic to casino-service), but this would add minor latency.

================================================================================
OPTIMIZATION 9: Security Stack Overhead
================================================================================

Falco: 400m CPU, 976Mi RAM (syscall tracing via eBPF)
Kyverno: ~15m CPU, ~213Mi RAM (4 pods, admission control)

Analysis:
  - Falco's 400m CPU is notable but expected for eBPF syscall monitoring
  - Kyverno is lightweight and only impacts pod admission (not data path)
  - Neither is in the hot request path (Falco monitors syscalls, Kyverno
    only fires on K8s API mutations)

Verdict: NO CHANGE (security infrastructure, not worth removing)

================================================================================
SUMMARY: FINAL RESULTS
================================================================================

Metric                    Baseline        After           Change
----------------------------------------------------------------------
wrk 100c req/s            38,003          111,987         +194.7%
wrk 500c req/s            96,143          105,247         +9.5%
wrk 1000c req/s           N/A             101,241         (new baseline)
curl TTFB                 4-5ms           4-5ms           no change
DNS 200 lookups           <1s             <1s             no change
Node CPU                  9%              12%             +3% (test load)
Conntrack                 6,809/4.2M      6,480/4.2M      stable

Changes applied:
  [x] CoreDNS: cache 300s, bufsize 1232, forward max_concurrent 2000
  [x] Kernel: tcp_fin_timeout = 10
  [x] K3s config: written but NOT restarted (requires maintenance window)
  [x] Nginx: SSL session cache 50m, proxy buffering, reset_timedout_connection
  [x] Varnish: grace 5m, keep 10m, static TTL 4h

Pending actions:
  [ ] Restart K3s to apply config.yaml (maintenance window required)
  [ ] Persist tcp_fin_timeout in /etc/sysctl.d/99-k3s-tuning.conf
  [ ] Consider Guaranteed QoS for nginx-tls pods under production load
  [ ] Consider reducing casino-service HPA minReplicas during off-peak

================================================================================
CONFIG FILES ON ops-host
================================================================================

/etc/rancher/k3s/config.yaml - K3s server tuning (pending restart)
CoreDNS ConfigMap (kube-system/coredns) - Updated in-cluster
Nginx ConfigMap (casino-prod/nginx-config) - Updated and rolled out
Varnish ConfigMap (casino-prod/varnish-vcl) - Updated and rolled out
