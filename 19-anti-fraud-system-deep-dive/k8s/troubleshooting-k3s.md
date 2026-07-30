# Troubleshooting: fraud-detection on K3s / k3d

Six gotchas hit while bringing the fraud-detection stack up on a fresh K3s
(specifically k3d) cluster. Each entry follows the same shape: **symptom**,
**diagnosis**, **root cause**, **fix**. None of these are documented in the
official Helm charts, and every one of them surfaces silently — so save
yourself the half-day and skim before you `helm install`.

> Companion script: [`apply-all.sh`](./apply-all.sh) already encodes the
> permanent fixes for all six. This document explains *why* each `--set` flag
> and `enableServiceLinks: false` is there.

---

## Gotcha A — cert-manager `startupapicheck` Pending forever (k3d)

**Symptom**

```text
$ kubectl -n cert-manager get pods
NAME                                      READY   STATUS    RESTARTS   AGE
cert-manager-startupapicheck-xxxxx        0/1     Pending   0          12m
```

`helm install --wait` hangs at the timeout. Other cert-manager pods are
`Running` and healthy.

**Diagnosis**

```bash
kubectl -n cert-manager describe pod -l app=startupapicheck
kubectl -n cert-manager logs -l app=startupapicheck --previous
# Look for: "dial tcp 10.43.0.1:443: connect: connection refused"
```

**Root cause**

k3d's kubelet proxy is not yet routing traffic to the apiserver when the
startup-check pod fires its first request. It's a race that shows up
consistently on cluster cold-start — never on bare-metal K3s.

**Fix**

Disable the check entirely; the controller and webhook do their own readiness:

```bash
helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --version v1.15.3 \
  --set installCRDs=true \
  --set startupapicheck.enabled=false \
  --wait
```

---

## Gotcha B — cert-manager validating webhook returns 502

**Symptom**

```text
Error from server (InternalError): error when creating "issuer.yaml":
Internal error occurred: failed calling webhook "webhook.cert-manager.io":
failed to call webhook: Post "https://cert-manager-webhook...":
proxy error from 127.0.0.1:6443 while dialing 10.42.X.X:10250, code 502
```

Any `kubectl apply` of a `ClusterIssuer`, `Issuer`, or `Certificate` is rejected.

**Diagnosis**

```bash
kubectl get pods -n cert-manager -o wide                # which node hosts webhook?
kubectl get nodes -o wide                                # how many nodes?
kubectl logs -n kube-system <k3s-server-pod> | grep 502  # apiserver -> kubelet
```

**Root cause**

In k3d the apiserver routes webhook calls via the kubelet proxy (`:10250`).
When the webhook pod lands on a worker node, the proxy hop fails; the master
can talk to its own kubelet only.

**Fix (workaround, immediate)**

Mark the webhook non-blocking so apply succeeds while the certificates are
still issued asynchronously:

```bash
kubectl patch validatingwebhookconfiguration cert-manager-webhook \
  --type=json \
  -p='[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}]'
```

**Fix (permanent)**

Pin the webhook to the master node so the proxy hop is local:

```yaml
helm install ... \
  --set webhook.nodeSelector."node-role\.kubernetes\.io/master"=true
```

---

## Gotcha C — Strimzi Kafka CR never reconciles, no events

**Symptom**

```text
$ kubectl -n fraud-detection get kafka
NAME          DESIRED KAFKA REPLICAS   READY
fraud-kafka                            <empty>

$ kubectl -n fraud-detection get pods
NAME                              READY   STATUS    RESTARTS   AGE
# (no Kafka pods at all)

$ kubectl -n fraud-detection describe kafka fraud-kafka
# ... no events, no status, nothing.
```

The `KafkaNodePool` CR is present, the operator pod is `Running`, and yet
nothing happens. Operator logs are silent past the leader-election line.

**Diagnosis**

```bash
kubectl -n strimzi-system logs deployment/strimzi-cluster-operator \
  | grep -E 'leader|elect|connection'
kubectl -n strimzi-system get networkpolicies
kubectl get clusterpolicies | grep -i deny       # Kyverno?
```

**Root cause**

If Kyverno is installed with `add-network-policy` (or any policy that
auto-generates a `default-deny` NetworkPolicy in every namespace), the
`strimzi-system` namespace ends up with egress restricted to DNS only. The
operator can't reach `kubernetes.default` (`10.43.0.1:443`), the leader-
election lease never gets written, and reconciliation never starts. No errors
are logged because the client-go retry loop is silent on connection refusal.

**Fix**

Allow apiserver egress in the operator namespace:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-apiserver-egress
  namespace: strimzi-system
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock:
            cidr: 10.43.0.1/32   # ClusterIP of kubernetes.default
      ports: [{protocol: TCP, port: 443}]
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]
      ports: [{protocol: TCP, port: 6443}]   # node IP fallback
    - ports: [{protocol: UDP, port: 53}]     # DNS
```

Apply the same policy in `elastic-system` and `cert-manager` if you see the
same silent stall. `apply-all.sh` already does this when Kyverno is detected.

---

## Gotcha D — schema-registry CrashLoopBackOff with only "PORT is deprecated"

**Symptom**

```text
$ kubectl -n fraud-detection logs deployment/schema-registry
PORT is deprecated. Please use SCHEMA_REGISTRY_LISTENERS instead.
$ # ...container exits with code 1, no further output.
```

The pod restarts every ~30 s. No `schema-registry.properties` is ever written.

**Diagnosis**

```bash
kubectl -n fraud-detection exec deployment/schema-registry -- env | grep -i port
# PORT=tcp://10.43.X.Y:8081       <-- this is the smoking gun
kubectl -n fraud-detection get svc schema-registry
```

**Root cause**

Kubernetes' `enableServiceLinks` feature (default **true**) injects env vars
for every Service in the same namespace whose name matches the container's.
Because there's a Service named `schema-registry`, the pod gets
`PORT=tcp://<svc-IP>:8081`. The `cp-schema-registry` 7.4 entrypoint script
treats *any* non-empty `PORT` as a fatal legacy-config conflict and `exit 1`s
**before** writing the properties file — so the only line in the log is the
deprecation warning.

**Fix**

```yaml
spec:
  template:
    spec:
      enableServiceLinks: false   # the one-liner that fixes everything
      containers:
        - name: schema-registry
          ...
```

Already applied in [`manifests/operators/schema-registry.yaml`](./manifests/operators/schema-registry.yaml).

---

## Gotcha E — Kibana CrashLoop: "serviceAccountToken cannot be specified when username is also set"

**Symptom**

```text
$ kubectl -n fraud-detection logs kibana-fraud-kibana-kb-xxxxx
[FATAL][root] Error: [config validation of [elasticsearch].username]:
"serviceAccountToken" cannot be specified when "username" is also set.
```

Kibana refuses to start. The `Kibana` CR shows `health: red`.

**Diagnosis**

```bash
kubectl -n fraud-detection get kibana fraud-kibana -o yaml | grep -A2 elasticsearch
kubectl -n fraud-detection get secret | grep kibana
# Look for fraud-kibana-kibana-user — that's the token ECK auto-injected.
```

**Root cause**

ECK's `elasticsearchRef` field auto-generates a service account token and
mounts it into the Kibana pod via the `kibana.yml` `elasticsearch.serviceAccountToken`
key. If you *also* set `config.elasticsearch.username` (even to an empty
string), Kibana's config validator hard-fails before the server starts.

**Fix**

Drop `elasticsearchRef`, drop every `elasticsearch.username` /
`elasticsearch.password` key, and point Kibana at ES directly:

```yaml
apiVersion: kibana.k8s.elastic.co/v1
kind: Kibana
metadata:
  name: fraud-kibana
spec:
  version: 8.11.0
  count: 1
  # NO elasticsearchRef
  config:
    elasticsearch.hosts:
      - "http://fraud-es-es-http.fraud-detection.svc.cluster.local:9200"
    elasticsearch.ssl.verificationMode: none
```

This works only because we run ES with `xpack.security.enabled: false`.
For a production-hardened ES, leave `elasticsearchRef` in and remove the
explicit `elasticsearch.*` keys instead.

---

## Gotcha F — Helm install denied by Kyverno: "limits required"

**Symptom**

```text
Error: INSTALLATION FAILED: admission webhook "validate.kyverno.svc-fail"
denied the request:
resource Pod/cert-manager/cert-manager-cainjector-xxxx was blocked due to
the following policies:
  require-pod-resources:
    autogen-validate-resources:
      'spec.containers[0].resources.limits' is required.
```

`helm upgrade --install` fails immediately. Affects cert-manager, Strimzi,
ECK, and Traefik because none of them set `limits` on every subcomponent
by default.

**Diagnosis**

```bash
kubectl get clusterpolicy
kubectl describe clusterpolicy require-pod-resources
```

**Root cause**

Kyverno's `require-pod-resources` policy enforces that *every* container has
both `requests` and `limits`. The default Helm values for these operators
include `requests` only, and several subcomponents (cainjector, webhook,
startupapicheck) have no `resources` block at all.

**Fix**

Pass `--set resources.limits.{cpu,memory}` for each subcomponent on every
operator install:

```bash
helm upgrade --install cert-manager jetstack/cert-manager \
  --set resources.limits.cpu=200m \
  --set resources.limits.memory=256Mi \
  --set webhook.resources.limits.cpu=200m \
  --set webhook.resources.limits.memory=128Mi \
  --set cainjector.resources.limits.cpu=200m \
  --set cainjector.resources.limits.memory=256Mi \
  --set startupapicheck.enabled=false \
  ...

helm upgrade --install strimzi-kafka-operator strimzi/strimzi-kafka-operator \
  --set resources.limits.cpu=500m --set resources.limits.memory=384Mi \
  ...

helm upgrade --install eck-operator elastic/eck-operator \
  --set resources.limits.cpu=1 --set resources.limits.memory=512Mi \
  ...

helm upgrade --install traefik traefik/traefik \
  --set resources.limits.cpu=500m --set resources.limits.memory=256Mi \
  ...
```

The full set of flags is already wired in [`apply-all.sh`](./apply-all.sh).

---

## Quick reference

| Symptom keyword                              | Gotcha |
|----------------------------------------------|:------:|
| `startupapicheck` Pending                    | A      |
| webhook 502 from `dialing 10.42.x.x:10250`   | B      |
| Kafka CR `READY=<empty>`, no pods, no events | C      |
| `PORT is deprecated` then exit               | D      |
| `serviceAccountToken cannot be specified`    | E      |
| admission denied "limits required"           | F      |
