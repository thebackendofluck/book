# HashiCorp Vault Secrets Management for iGaming K8s Services

Production-derived Terraform configuration for managing HashiCorp Vault in a
multi-environment, multi-jurisdiction iGaming platform. Uses Terraform Cloud
for state management and implements Kubernetes auth backend for zero-secret
pod deployments.

## Architecture

```
envs/
  dev/                  # Development environment
    backend.tf          # Terraform Cloud workspace
    config.tf           # Vault auth backend + secret mount
    policy.tf           # Per-namespace read-only policies
    roles.tf            # K8s service account -> Vault role bindings
    variables.tf        # Application list with namespace/service_account mapping
    provider.tf         # Vault provider configuration
    secrets/            # Encrypted secret resources per application
      backoffice.tf     # Backoffice app secrets
  nj/                   # New Jersey production (per-jurisdiction)
    backend.tf          # Separate TFC workspace per jurisdiction
    config.tf           # Isolated Vault instance per state
    policy.tf           # Simplified policy for production
    roles.tf            # Direct service account bindings
    variables.tf        # NJ-specific applications (platform, payments, global-id)
```

## Key Concepts

- **Per-jurisdiction Vault instances**: Each US state's gaming commission requires
  isolated secret storage. NJ, PA, and other states each get their own Vault
  backend and Terraform workspace.
- **K8s auth backend**: Pods authenticate to Vault using their Kubernetes service
  account token -- no secrets in environment variables or config files.
- **Policy-per-namespace**: Each K8s namespace gets a Vault policy granting
  read-only access to `secret/<namespace>/*`. This ensures backoffice services
  can't read payment secrets and vice versa.
- **Vaulted encryption**: Secrets are encrypted at rest in Terraform state
  using asymmetric encryption (public key in repo, private key in TFC variables).

## Source

Adapted from production Vault infrastructure managing secrets for 5 environments
(dev, stage, infra, NJ prod, PA prod) across a multi-state US iGaming platform.
All URLs, tokens, and encrypted payloads have been sanitized.
