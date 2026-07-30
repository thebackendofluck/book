# Karpenter NodePools — AcmetoCasino sizing

These NodePools are **customized to the platform architecture and to the size of
each environment** — they are not a generic copy/paste. The architecture splits
the fleet into three tiers, and the *limits* (the maximum compute Karpenter may
provision per pool) are tuned per environment so a runaway HPA/KEDA loop can
never provision the whole AWS account.

## Tiers (architecture-driven)

| NodePool | Workloads | Arch | Capacity | EC2NodeClass | Disruption stance |
|---|---|---|---|---|---|
| `system` | CoreDNS, metrics-server, CNI/netpol agents, observability | amd64 | on-demand | `casino-default` | consolidation capped at 1 node, weekend freeze |
| `stateful-amd64` | certified RNG/game-math engine, in-progress sessions, HSM-adjacent | **amd64 only** | **on-demand only** | `casino-certified` | `WhenEmpty` only, `expireAfter: Never`, budget 1 node |
| `stateless-arm64` | FastAPI, dashboards, Kafka consumers, bet-spike workers | **arm64 (Graviton)** | spot-first + on-demand | `casino-default` | aggressive, with weekend-peak freeze |

Rationale: the certified/money path must never move architecture (re-certification
risk) or ride spot (mid-spin reclaim = dispute); the bulk stateless tier takes the
Graviton + spot savings; system add-ons stay small and steady.

## Two EC2NodeClasses, and pinned AMIs

`ec2nodeclass.yaml` defines two node templates, not one:

- `casino-default` for `system` and `stateless-arm64`, recycled weekly by
  `expireAfter: 168h`. AMI bumps here are ordinary platform work.
- `casino-certified` for `stateful-amd64` only. The AMI under a certified binary
  is part of the tested configuration, so this pin moves only after the test lab
  re-runs the certification suite.

Both pin an explicit `alias: al2023@vYYYYMMDD`. **Never `@latest`**: a floating
alias means Karpenter adopts each new EKS-optimised release and drift-replaces
nodes on its own schedule, which is an untracked change to the runtime under the
money path and leaves "which kernel was that node on?" unanswerable after the node
is gone. A single shared node class has the same problem in a different shape: a
routine web-tier patch re-images the certified nodes too.

Resolve current releases before bumping, and bump in a reviewed PR:

```bash
aws ssm get-parameters-by-path --recursive \
  --path /aws/service/eks/optimized-ami/1.31/amazon-linux-2023 \
  --query 'Parameters[].Name'
```

The alias is Kubernetes-version sensitive: on a control-plane upgrade Karpenter
re-resolves the pinned version for the new minor. For the certified tier, replace
the alias with the immutable `- id: ami-...` once the lab signs off, so not even a
K8s upgrade moves it.

## Disruption budgets and PodDisruptionBudgets

Every pool carries a `budgets` block, including `system`. `WhenEmptyOrUnderutilized`
with no budget is unbounded churn on the pool hosting CoreDNS and the VPC CNI
network-policy agent, and a simultaneous restart of that agent means the
default-deny policies in `../security/` are briefly not enforced.

Budgets and PDBs bound different things and neither replaces the other: `budgets`
caps how many **nodes** Karpenter disrupts at once, `pdb.yaml` caps how many
**replicas** of a workload may be evicted at once. Both, or the money path can
still go dark inside a single compliant node disruption. Note that a PDB blocks
only *voluntary* disruption (consolidation, drift, expiration): spot interruption,
node failure and node repair ignore it, as does `karpenter.sh/do-not-disrupt`.

After applying, `ALLOWED DISRUPTIONS` must be at least 1 for every entry, or nodes
holding those pods can never be drained:

```bash
kubectl get pdb -A
```

## Sizing per environment

Edit the `spec.limits` (and the `role`/discovery tag in `ec2nodeclass.yaml`) per
environment. Suggested starting points, sized to expected peak concurrency:

| Pool | staging `cpu` / `memory` | prod `cpu` / `memory` | prod instance sizes |
|---|---|---|---|
| `system` | 8 / 32Gi | 32 / 128Gi | large–xlarge |
| `stateful-amd64` | 64 / 256Gi | 400 / 1600Gi | xlarge–4xlarge |
| `stateless-arm64` | 200 / 800Gi | 2000 / 8000Gi | (category c/m/r, gen >6) |

The committed manifests carry the **staging** values; the prod values are in the
comments next to each `limits` block. For very large events (see Chapter 41 —
World Cup), raise the `stateless-arm64` cpu limit and **remove or shorten the
weekend freeze budget** for the event window.

## Apply

```bash
# 1) Provision the AWS scaffolding (cluster, node + Karpenter IAM, interruption
#    queue, ECR) — see ../../terraform.
# 2) Install Karpenter (helm) bound to the controller role + interruption queue
#    output by Terraform (karpenter_controller_role_arn, interruption_queue_name).
# 3) Apply these manifests:
kubectl apply -f ec2nodeclass.yaml          # casino-default + casino-certified
kubectl apply -f nodepool-system.yaml
kubectl apply -f nodepool-stateful-amd64.yaml
kubectl apply -f nodepool-stateless-arm64.yaml
kubectl apply -f pdb.yaml                   # replica floors for the workloads
```

Pods select their tier by tolerating the `workload-class=stateful` taint (money
path) or by an `nodeSelector: { kubernetes.io/arch: amd64 }` pin for the
certified engine; everything else defaults to the cheaper `stateless-arm64` pool.
