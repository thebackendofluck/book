# RustFS-HA on K3s: Test Results

**Date:** 2026-03-30
**Cluster:** K3s v1.34.5+k3s1 on ops-host (6 nodes: 3 masters + 3 workers)
**Namespace:** storage
**RustFS version:** 1.0.0-alpha.90

## Deployment

### Image
- Built from `/opt/rustfs-ha/Dockerfile` — downloads upstream `rustfs` binary (v1.0.0-alpha.90, x86_64-musl)
- Image: `docker.io/library/rustfs-ha:latest` (436 MB)
- Imported via `k3s ctr images import` to all 6 cluster nodes

### K8s Resources
- **rustfs** (primary): Deployment, 1 replica → worker-002 (10.42.2.241)
- **rustfs-ha2** (secondary): Deployment, 1 replica → worker-003 (10.42.3.214)
- **PodDisruptionBudget**: minAvailable=1 for HA maintenance safety
- **PVC per instance**: 5Gi data + 1Gi logs (local-path StorageClass)
- **Service**: rustfs-svc NodePort 9000→32536, 9001→32369

### Helm Command
```bash
helm install rustfs ./helm/rustfs -n storage \
  --set replicaCount=1 \
  --set image.rustfs.repository=docker.io/library/rustfs-ha \
  --set image.rustfs.tag=latest \
  --set image.rustfs.pullPolicy=Never \
  --set mode.standalone.enabled=true \
  --set mode.distributed.enabled=false \
  --set secret.rustfs.access_key=acmetocasino \
  --set secret.rustfs.secret_key=rustfs-secret-2026 \
  --set storageclass.name=local-path \
  --set storageclass.dataStorageSize=5Gi \
  --set storageclass.logStorageSize=1Gi \
  --set resources.limits.memory=2Gi \
  --set resources.limits.cpu=1 \
  --set resources.requests.memory=512Mi \
  --set resources.requests.cpu=250m
```

## S3 Compatibility Tests

Endpoint: `http://10.0.10.45:32536`
Credentials: `acmetocasino` / `rustfs-secret-2026`

| # | Test | Result | Notes |
|---|------|--------|-------|
| 1 | Create bucket | PASS | `aws s3 mb s3://test-bucket` |
| 2 | List buckets | PASS | Returns bucket with creation timestamp |
| 3 | Upload small object | PASS | 8 bytes, correct transfer |
| 4 | List bucket contents | PASS | Object listed with correct size/time |
| 5 | Download object | PASS | Content fetched correctly |
| 6 | Verify content integrity | PASS | `diff` on /etc/hostname exact match |
| 7 | Delete object | PASS | `aws s3 rm` |
| 8 | Delete bucket | PASS | `aws s3 rb` |
| 9 | Multipart upload (20MB) | PASS | ETag `*-3` confirms 3-part MPU |
| 10 | Multipart download (20MB) | PASS | Transfer completed |
| 11 | Verify large file MD5 | PASS | `3fb7f7dc921e0a3170762fb95b2e97bd` matches |
| 12 | HeadObject | PASS | Returns ContentLength, ETag, StorageClass |
| 13 | Enable versioning | PASS | Status=Enabled |
| 14 | Get bucket versioning | PASS | Returns `{"Status":"Enabled"}` |
| 15 | Upload 3 versions of same key | PASS | 3 distinct VersionId UUIDs |
| 16 | List object versions | PASS | All 3 versions with IsLatest flag |
| 17 | Get specific version | PASS | Oldest version returned "version1\n" |
| 18 | Presigned URL (GET) | PASS | HTTP 200 via curl on presigned URL |
| 19 | ListBuckets API | PASS | Returns owner ID and creation date |
| 20 | Copy object (same bucket) | PASS | `s3 cp s3://...` to `s3://...` |
| 21 | Object metadata (user-defined) | PASS | x-test, x-version preserved in HEAD |
| 22 | Put object tagging | PASS | TagSet stored |
| 23 | Get object tagging | PASS | Returns env=test, project=rustfs |
| 24 | List objects with prefix | PASS | Filter by prefix works |
| 25 | Bucket lifecycle configuration | PASS | Expiration rule created |
| 26 | Get bucket lifecycle | PASS | Rule returned correctly |

**All 26 S3 compatibility tests: PASS (0 failures)**

## Performance Benchmarks

Platform: ops-host → K3s worker (10.0.10.45) over 1GbE LAN
Test file: 20MB random binary (`/dev/urandom`)

### Write Throughput (10 concurrent 20MB uploads)
- Total: 200MB
- Time: 22,933ms
- **Throughput: 8.7 MB/s**
- Note: Bottleneck is local-path StorageClass (single-node NVMe I/O x 10 concurrent writes)

### Read Throughput (10 concurrent 20MB downloads)
- Total: 200MB
- Time: 9,500ms
- **Throughput: 21.0 MB/s**
- Read is ~2.4x faster than write (expected for NVMe-backed local-path)

### Resource Usage (steady-state)
- CPU: 165m (16.5% of 1 core limit)
- Memory: 840Mi (out of 2Gi limit)

## Issues Found and Fixed

| Issue | Resolution |
|-------|-----------|
| Helm set with hyphenated image name (`rustfs ha` misinterpreted as space by Helm) | Used proper quoting: `--set 'image.rustfs.repository=docker.io/library/rustfs-ha'` |
| Standalone mode creates only 1 replica (RWO PVC constraint) | Deployed second independent Helm release (`rustfs-ha2`) on separate worker node |
| No PodDisruptionBudget | Added PDB with `minAvailable: 1` |

## Architecture Notes

- **HA mode used**: Two independent standalone instances on different worker nodes (worker-002 and worker-003)
- **Not used**: Distributed erasure-coded mode (requires consistent volumes, more complex setup)
- **PVC**: local-path (RWO) — each instance has its own data volume; no shared state
- **Production recommendation**: For shared-state HA, use a distributed block storage (Longhorn, Rook/Ceph) with RWX access mode or use RustFS's native distributed mode with StatefulSet

## Cluster Health (post-deployment)

```
NAME                    STATUS   ROLES                  AGE   VERSION
k3s-casino-master-001   Ready    control-plane,etcd     5d    v1.34.5+k3s1
k3s-casino-master-002   Ready    control-plane,etcd     5d    v1.34.5+k3s1
k3s-casino-master-003   Ready    control-plane,etcd     5d    v1.34.5+k3s1
k3s-casino-worker-001   Ready    <none>                 5d    v1.34.5+k3s1
k3s-casino-worker-002   Ready    <none>                 5d    v1.34.5+k3s1
k3s-casino-worker-003   Ready    <none>                 5d    v1.34.5+k3s1
```

All existing workloads in other namespaces (acmetocasino, acmetocasino-prod, kong, argocd, etc.) unaffected.
