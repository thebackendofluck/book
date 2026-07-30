# Staged Deployment Plan — K3s / k3d Edition

This is the K8s-flavoured companion to [`../deployment/staged_deployment_plan.md`](../deployment/staged_deployment_plan.md).
Where the original document covers the AWS / EKS / Databricks production
target, this one covers a self-managed K3s or k3d cluster — the path most
operators will use for the on-prem build.

> The phases below are layered on top of the existing 7-phase plan. Phase 0.5
> is **new** and **k3d/K3s-specific**. Skip it on EKS/GKE/AKS where managed
> ingress, storage, and admission policies behave very differently.

---

## Phase 0.5 — k3d / K3s pre-flight (NEW)

Before you run `apply-all.sh prereqs`, confirm five environmental properties.
Half the gotchas in [`troubleshooting-k3s.md`](./troubleshooting-k3s.md) come
from violating one of these silently.

### 0.5.1 Verify Kyverno admission policies

If Kyverno is installed cluster-wide, every Helm chart you install will be
re-validated against its `ClusterPolicy` set. The two that bite the most:

- `require-pod-resources` — denies any pod missing `resources.limits`
  (Gotcha F).
- `add-network-policy` — auto-creates a default-deny `NetworkPolicy` in every
  new namespace, which silently blocks operator -> apiserver traffic
  (Gotcha C).

```bash
kubectl get clusterpolicies
kubectl get clusterpolicies -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.validationFailureAction}{"\n"}{end}'
```

If you see policies in `Enforce` mode, plan to pass `--set resources.limits.*`
on every operator install **and** add an `allow-apiserver-egress`
NetworkPolicy in operator namespaces (`apply-all.sh` does this automatically
when it detects Kyverno).

### 0.5.2 Verify storage class

K3s ships with `local-path` (Rancher local provisioner). It is RWO and
node-bound. That has two consequences:

- Statefulsets that use `local-path` PVCs are pinned to a single node.
  After a node failure they will not reschedule unless you manually delete the PV.
- Multi-node Kafka (Strimzi) and multi-node Elasticsearch (ECK) cannot share
  storage — every replica must claim its own PV on its own node.

```bash
kubectl get storageclass
kubectl get storageclass -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.provisioner}{"\t"}{.reclaimPolicy}{"\n"}{end}'
```

For the chapter-19 stack we accept the single-node limitation in dev/k3d.
For prod, install `longhorn` or `openebs` and patch
`manifests/operators/strimzi-kafka.yaml` + `eck-elasticsearch.yaml` storage
classes accordingly.

### 0.5.3 Verify cluster type (k3d vs bare-metal K3s)

```bash
kubectl get nodes -o wide
kubectl get nodes -o wide | grep -E 'k3d|k3s'
```

- **k3d** nodes have `INTERNAL-IP` in the Docker bridge range
  (`172.18.0.x`) and `OS-IMAGE` like `Alpine`. Host LAN cannot reach pod IPs
  unless you mapped ports at cluster create time
  (`k3d cluster create --port 443:30443@loadbalancer`).
- **Bare-metal K3s** nodes have real LAN IPs. Ingress/NodePort services
  are reachable from the LAN immediately.

This single distinction drives the entire ingress story below (0.5.4).

### 0.5.4 Verify ingress controller

Bare-metal K3s ships with Traefik enabled by default at `:80` and `:443`.
**k3d does not** — its built-in load balancer is a separate container that
only forwards ports you explicitly mapped at cluster create.

```bash
kubectl get pods -n kube-system | grep -E 'traefik|nginx|haproxy'
kubectl get svc -A -o wide | grep -E 'LoadBalancer|NodePort'
```

If nothing shows up, `apply-all.sh prereqs` will install Traefik for you.
But you still need to decide how the host reaches it — pick one:

1. Recreate the k3d cluster with `--port 443:30443@loadbalancer`.
2. `kubectl -n traefik port-forward svc/traefik 8443:443` (development only).
3. Install MetalLB and bind a LAN IP (requires Docker network on host).

### 0.5.5 Allow apiserver egress in operator namespaces

If 0.5.1 found Kyverno (or you have any cluster-wide `default-deny`), apply
the NetworkPolicy from [`troubleshooting-k3s.md` Gotcha C](./troubleshooting-k3s.md#gotcha-c--strimzi-kafka-cr-never-reconciles-no-events)
in every operator namespace **before** the operator pod starts:

```bash
for ns in strimzi-system elastic-system cert-manager; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done
# then apply allow-apiserver-egress.yaml in each
```

Skip this and you will spend an hour staring at a Kafka CR that says nothing
and an operator log that ends at the leader-election line.

---

## Phase 1..7

Continue with the standard plan in
[`../deployment/staged_deployment_plan.md`](../deployment/staged_deployment_plan.md).
The K3s implementation of phases 1-2 (infrastructure + core services) is
mechanised by:

```bash
./apply-all.sh --phase prereqs   # phase 1: cluster-wide operators
./apply-all.sh --phase data      # phase 2: Postgres / Redis / Kafka / ES
./apply-all.sh --phase apps      # phase 3-4: app Deployments + monitoring
./apply-all.sh --phase verify    # phase 7: post-deployment checks
```

Each sub-phase of the script is idempotent and safe to re-run.
