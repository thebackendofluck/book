# Config Data - Centralized Configuration Repository

Production-derived example of a centralized configuration repository used to manage
environment-specific settings across an iGaming platform.

## Context (Chapter 7 - Casino Implementation Planning Timeline)

When planning a casino platform launch, one of the earliest infrastructure decisions
is how to manage configuration across environments (dev, stage, prod). This repository
demonstrates the pattern of storing all environment-aware configuration in a single
versioned repository, separate from application code.

## Structure

```
config-data/
  brands/          # Per-brand configuration (white-label settings)
    brand.conf     # Template brand configuration
  games/           # Game provider configuration
  services/        # Shared service configuration (mailer, etc.)
```

## Key Concepts

- **Environment Isolation**: Each config block defines stage vs. prod settings
  with different database URLs, credentials, and service endpoints
- **Brand Separation**: White-label brands each get their own configuration file,
  enabling multi-tenant deployments from a single codebase
- **Service Configuration**: Shared services (mailer, notifications) are configured
  centrally to avoid duplication across microservices

## Usage

Application services reference this repository (typically as a Git submodule or
via a configuration server) and load the appropriate environment block at startup.

## Security Note

In production, sensitive values (passwords, API keys) should be injected via
a secrets manager (e.g., HashiCorp Vault) rather than stored in config files.
The passwords shown here are placeholder examples only.
