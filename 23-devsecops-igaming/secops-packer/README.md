# SecOps Packer Image Pipeline

Production-derived Packer pipeline for building security-hardened AMIs and container
images for regulated iGaming infrastructure. Uses Jenkins for orchestration, building
multiple OS variants (Ubuntu, RHEL, CentOS, Amazon Linux) with CIS-benchmark hardening
scripts applied at image build time.

## Architecture

```
ContainerBuilds/
  Ubuntu20ContainerImage/    # Hardened Ubuntu 20.04 Docker base image
    build.pkr.hcl            # Packer Docker builder with ECR push
    ubuntu.sh                # CIS hardening script entry point
    scripts/                 # 20+ modular hardening functions

MachineImageBuilds/
  Ubuntu20MachineImage/      # Hardened Ubuntu 20.04 EC2 AMI
    build.pkr.hcl            # Packer Amazon EBS builder with KMS encryption
    variables.pkr.hcl        # Variables: regions, KMS keys, instance type
    install.sh               # Full hardening: SSM, Datadog, 40+ security functions

Jenkinsfile                  # Multi-stage pipeline: containers then AMIs
```

## Key Concepts

- **Immutable infrastructure**: All security hardening is baked into images at build
  time. No runtime configuration drift.
- **Multi-region AMI distribution**: AMIs are encrypted with per-region KMS keys and
  copied to all operating regions (us-east-1, eu-west-1, eu-west-2, ca-central-1).
- **CIS hardening at scale**: The install.sh script executes 40+ hardening functions
  covering kernel, filesystem, SSH, audit, firewall, and user management.
- **Cross-account sharing**: AMIs are shared across multiple AWS accounts for
  dev/stage/prod isolation.

## Source

Adapted from production SecOps pipeline building monthly hardened images for
a regulated US iGaming platform. All AWS account IDs, KMS ARNs, and ECR
repositories have been sanitized.
