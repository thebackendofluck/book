# RustFS-HA Performance Benchmark Report

**Date:** 2026-03-30
**Environment:** K3s cluster on ops-host (10.0.10.42)
**Endpoint:** http://10.0.10.42:32536
**Test origin:** ops-host host (same-network, minimal network latency)
**Data source:** /dev/urandom (real random data, not zeros)

---

## Summary Comparison Table

| Metric | RustFS-HA (K3s) | MinIO/RustFS prev. | AWS S3 (reference) |
|--------|----------------|--------------------|--------------------|
| Write 100MB (sequential) | 35.08 MB/s | N/A | ~100 MB/s |
| Read 100MB (sequential) | 77.27 MB/s | 21 MB/s (from K3s agent) | ~200 MB/s |
| Write 200MB (multipart) | 31.34 MB/s | N/A | ~100 MB/s |
| Concurrent 10x20MB write | 39.58 MB/s | 8.7 MB/s write | - |
| Concurrent 10x20MB read | 83.19 MB/s | - | - |
| PUT IOPS (1KB, 50-concurrent) | 32 ops/s | N/A | ~3500 ops/s |
| LIST 1000 objects | 4458 ms | N/A | ~200ms |
| GET p50 (1KB) | 900 ms | N/A | ~20ms |
| GET p95 (1KB) | 927 ms | N/A | ~35ms |
| GET p99 (1KB) | 940 ms | N/A | ~50ms |

---

## Test 1: Sequential Throughput

### Write (single upload)

| File Size | Time (ms) | Throughput |
|-----------|-----------|------------|
| 1 MB | 900 ms | 1.11 MB/s |
| 5 MB | 949 ms | 5.26 MB/s |
| 10 MB | 1106 ms | 9.04 MB/s |
| 50 MB | 1987 ms | 25.16 MB/s |
| 100 MB | 2850 ms | 35.08 MB/s |

**Observation:** There is a significant fixed overhead per request (~870–900ms baseline latency). Large files benefit from higher throughput as the per-request overhead is amortized. The 100MB sequential write reaches 35 MB/s.

### Read (single download)

| File Size | Time (ms) | Throughput |
|-----------|-----------|------------|
| 1 MB | 914 ms | 1.09 MB/s |
| 5 MB | 926 ms | 5.39 MB/s |
| 10 MB | 991 ms | 10.09 MB/s |
| 50 MB | 1122 ms | 44.56 MB/s |
| 100 MB | 1294 ms | 77.27 MB/s |

**Observation:** Read throughput is substantially better than write at large sizes (77 MB/s vs 35 MB/s for 100MB). The same high baseline latency pattern applies — reads under 10MB are dominated by the ~900ms per-request overhead.

---

## Test 2: Concurrent Throughput (10 parallel clients)

| Test | Duration | Aggregate Throughput |
|------|----------|----------------------|
| 10x 20MB uploads | 5053 ms | 39.58 MB/s |
| 10x 20MB downloads | 2404 ms | 83.19 MB/s |

**Observation:** Concurrent uploads scale well — aggregate write throughput nearly matches sequential read throughput (39.58 MB/s vs 35.08 MB/s for 100MB single). Concurrent reads hit 83 MB/s aggregate, showing good parallelism. This is a **4.6x improvement** over the previous test's 8.7 MB/s concurrent write result.

---

## Test 3: Small Object IOPS (1 KB objects)

| Operation | Result |
|-----------|--------|
| 1000 PUT (batched 50-concurrent) | 30545 ms total → **32 ops/s** |
| LIST 1000 objects | 4458 ms |

**Observation:** Small object performance is poor. At ~900ms per round-trip overhead, parallel batches of 50 yield only 32 ops/s. This is a critical weakness versus AWS S3 (~3500 ops/s). The high per-operation latency suggests the RustFS-HA setup has significant overhead per request, likely from the K3s NodePort → Service → Pod routing chain or storage backend fsync behavior.

---

## Test 4: Multipart Upload (200 MB)

| File Size | Duration | Throughput |
|-----------|----------|------------|
| 200 MB | 6381 ms | 31.34 MB/s |

**Observation:** Multipart upload of 200MB achieves 31.34 MB/s, slightly below the 35 MB/s sequential 100MB write. The AWS CLI auto-selects multipart for files >8MB by default; the overhead here is roughly consistent with the sequential write pattern.

---

## Test 5: GET Latency (100 sequential 1KB GETs)

| Percentile | Latency |
|------------|---------|
| p50 | 900 ms |
| p95 | 927 ms |
| p99 | 940 ms |
| Min | 858 ms |
| Max | 952 ms |

**Observation:** Latency distribution is extremely tight (858ms–952ms range, only 94ms spread across 100 samples). This indicates the ~900ms baseline is a consistent overhead, not network jitter. Possible causes: RustFS sync-on-write behavior, K3s NodePort overhead, or a single slow storage tier. At p99=940ms, this is **~19x slower** than AWS S3 p99 (~50ms) for small object GETs.

---

## Improvement vs. Previous RustFS Tests

| Metric | Previous | Current | Delta |
|--------|----------|---------|-------|
| Concurrent write (10x20MB) | 8.7 MB/s | 39.58 MB/s | **+355%** |
| Read 100MB (sequential) | 21 MB/s | 77.27 MB/s | **+268%** |

The HA deployment on K3s shows dramatic throughput improvements for large-file concurrent workloads compared to the earlier single-node RustFS test. The improvement likely reflects the distributed replication design allowing parallel reads from multiple replicas.

---

## Analysis

### Strengths
- **Large file read throughput** is excellent: 77 MB/s sequential, 83 MB/s concurrent at 10 clients
- **Concurrent write throughput** improved 4.6x vs previous test (39 MB/s vs 8.7 MB/s)
- **Throughput scales with file size** — 100MB objects perform well
- **Concurrent read parallelism** works as expected with multiple replicas

### Weaknesses
- **High per-request latency** (~900ms baseline) kills small-object performance entirely
  - PUT IOPS: 32 ops/s vs AWS S3's ~3500 ops/s (100x gap)
  - GET p99: 940ms vs AWS S3's ~50ms (19x gap)
  - LIST 1000 objects: 4.5 seconds vs AWS S3's ~200ms (22x gap)
- **Not suitable for workloads requiring high IOPS or low latency metadata operations**
  - Game asset serving with many small files would be severely impacted
  - Any application polling for object existence or listing frequently would stall

### Recommended Use Cases (Given Current Performance)
- Large media file storage (video segments, backup archives, large binaries)
- Batch processing workloads where high throughput matters more than latency
- Cold/warm storage tiers where access latency SLAs are relaxed

### Not Recommended For
- Real-time game asset delivery (high-frequency small object GETs)
- High-IOPS metadata workloads (frequent listing, existence checks)
- Any latency-sensitive path under 500ms SLA

---

## Environment Details

| Parameter | Value |
|-----------|-------|
| Cluster | K3s on ops-host |
| RustFS endpoint | http://10.0.10.42:32536 (NodePort) |
| Access key | acmetocasino |
| Test data | /dev/urandom (non-compressible) |
| AWS CLI version | 2.34.19 |
| Host OS | Ubuntu 24 (kernel 6.8.0-106-generic) |
| Host load at test start | 13.68 (1m avg) |

> Note: The host was under moderate load (load avg ~13–16) during testing, which may have marginally affected results. The latency baseline of ~900ms is likely partially attributable to K3s service routing overhead and RustFS write durability settings, rather than purely host load.
