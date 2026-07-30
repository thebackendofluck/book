#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# S1 T14: Deploy Falco to K3s cluster on ops-host + 3 initial iGaming rules
# Uses official Falco Helm chart
set -euo pipefail

OPS_HOST="root@10.0.0.11"
NAMESPACE=falco
LOG=/var/log/security-remediations.log

echo "[$(date -Is)] S1-T14: Deploying Falco to K3s" | tee -a "${LOG}"

ssh "${OPS_HOST}" 'bash -s' <<'REMOTE'
set -euo pipefail

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Add Falco helm repo
helm repo add falcosecurity https://falcosecurity.github.io/charts 2>/dev/null || true
helm repo update

kubectl create namespace falco --dry-run=client -o yaml | kubectl apply -f -

# Create custom rules ConfigMap
cat > /tmp/falco-igaming-rules.yaml <<'RULES'
apiVersion: v1
kind: ConfigMap
metadata:
  name: falco-igaming-rules
  namespace: falco
data:
  igaming_rules.yaml: |
    # Rule 1: Shell spawned inside a database pod
    - rule: shell_in_db_pod
      desc: >
        A shell was spawned inside a Postgres or Redis container — this is
        never expected in production and indicates active compromise or
        lateral movement attempt.
      condition: >
        spawned_process and
        container and
        (container.image.repository contains "postgres" or
         container.image.repository contains "redis") and
        proc.name in (shell_binaries)
      output: >
        Shell spawned in DB pod (user=%user.name pod=%k8s.pod.name
        ns=%k8s.ns.name image=%container.image.repository
        cmd=%proc.cmdline)
      priority: CRITICAL
      tags: [igaming, database, shell, T1059]

    # Rule 2: pg_dump executed outside allowed time window
    - rule: pg_dump_unauthorized
      desc: >
        pg_dump executed in a container — legitimate backups run from the
        host cron, not from within containers. This indicates data exfiltration.
      condition: >
        spawned_process and
        container and
        proc.name = "pg_dump"
      output: >
        pg_dump in container (user=%user.name pod=%k8s.pod.name
        ns=%k8s.ns.name image=%container.image.repository
        cmdline=%proc.cmdline parent=%proc.pname)
      priority: CRITICAL
      tags: [igaming, exfiltration, T1005]

    # Rule 3: Redis FLUSHALL / FLUSHDB called
    - rule: redis_flushall
      desc: >
        redis-cli FLUSHALL or FLUSHDB was called inside a container —
        this would destroy all active player sessions and cause mass logout.
        Indicates destructive insider action or ransomware.
      condition: >
        spawned_process and
        container and
        proc.name = "redis-cli" and
        (proc.args contains "FLUSHALL" or proc.args contains "FLUSHDB")
      output: >
        Redis FLUSH command in container (user=%user.name pod=%k8s.pod.name
        ns=%k8s.ns.name cmd=%proc.cmdline)
      priority: CRITICAL
      tags: [igaming, destruction, T1485]
RULES

kubectl apply -f /tmp/falco-igaming-rules.yaml

# Install Falco with custom rules mounted
helm upgrade --install falco falcosecurity/falco \
  --namespace "${NAMESPACE}" \
  --set driver.kind=modern_ebpf \
  --set falcosidekick.enabled=true \
  --set falcosidekick.config.syslog.enabled=true \
  --set falcosidekick.config.syslog.host=127.0.0.1 \
  --set falcosidekick.config.syslog.port=514 \
  --set falcosidekick.config.syslog.protocol=udp \
  --set-string 'extraVolumes[0].name=igaming-rules' \
  --set-string 'extraVolumes[0].configMap.name=falco-igaming-rules' \
  --set-string 'extraVolumeMounts[0].name=igaming-rules' \
  --set-string 'extraVolumeMounts[0].mountPath=/etc/falco/rules.d' \
  --wait --timeout 5m

echo ""
echo "Falco deployment status:"
kubectl get pods -n "${NAMESPACE}"

echo ""
echo "Verify rules loaded:"
kubectl exec -n "${NAMESPACE}" \
  "$(kubectl get pod -n ${NAMESPACE} -l app.kubernetes.io/name=falco -o jsonpath='{.items[0].metadata.name}')" \
  -- falco --list 2>/dev/null | grep -E "shell_in_db_pod|pg_dump_unauthorized|redis_flushall" || echo "Check logs: kubectl logs -n falco -l app.kubernetes.io/name=falco"
REMOTE

echo "[$(date -Is)] S1-T14: Falco deployed" | tee -a "${LOG}"
