# vault/policies/casino-app.hcl
# Policy for pods running inside the cluster (via Kubernetes auth)
# Much more restrictive — no token generation, read-only

path "secret/data/casino/{{ identity.entity.aliases.auth_kubernetes_accessor.metadata.cluster_color }}/*" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}
