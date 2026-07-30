# vault/policies/casino-cluster-provisioner.hcl
# Policy for the CI/CD identity that provisions new clusters

path "secret/data/casino/+/postgres" {
  capabilities = ["read"]
}

path "secret/data/casino/+/redis" {
  capabilities = ["read"]
}

path "secret/data/casino/+/jwt-signing" {
  capabilities = ["read"]
}

path "secret/data/casino/+/k3s-join-token" {
  capabilities = ["read", "create", "update"]
  # Provisioner generates a new join token for each cluster
}

# Explicitly deny write to anything outside the + (color) prefix
path "secret/data/casino/*" {
  capabilities = ["deny"]
}

path "secret/metadata/*" {
  capabilities = ["list", "read"]
}
