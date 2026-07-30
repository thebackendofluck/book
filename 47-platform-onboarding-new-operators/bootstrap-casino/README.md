# Chapter 47 Bootstrap Scripts

This directory points operators to the repository bootstrap lane used by the
Chapter 47 onboarding process.

Primary implementation:

```text
new-platform/bootstrap-casino/bootstrap_casino.py
```

Example:

```bash
python3 new-platform/bootstrap-casino/bootstrap_casino.py \
  --casino-id acme-br-staging \
  --brand-name "Acme Brazil Staging" \
  --jurisdiction br \
  --domain acme-br.staging.internal \
  --environment staging \
  --runtime k3s \
  --output out/bootstrap-jurisdictions \
  --force
```

The script creates an artifact bundle only. pfSense, OpenBao, SSO, Grafana and
Kubernetes changes remain gated operator actions.

## Fake Provider Bootstrap

For staging validation on `secondary-host`, use:

```bash
writing/new-book/scripts/chapter-47/bootstrap-casino/apply-fake-secondary-host.sh
```

It generates a normal bundle and creates fake BAO, internal DNS and Grafana state
under `/tmp/casino-bootstrap-fake-state`. This validates the integration contract
without calling real infrastructure APIs.

## Real Provider Plan And Apply

Generate a real provider plan for `secondary-host`:

```bash
writing/new-book/scripts/chapter-47/bootstrap-casino/plan-real-secondary-host.sh
```

This writes `/tmp/casino-bootstrap-real-plans/secondary-host-br-staging.real-infra-plan.json`
and makes no provider API calls.

Real apply requires explicit approval and credentials in the environment:

```bash
YES_REAL_APPLY=1 \
BAO_ADDR=https://bao.example.internal \
BAO_TOKEN=... \
PFSENSE_URL=https://10.0.10.1 \
PFSENSE_API_KEY=... \
GRAFANA_URL=https://grafana.example.internal \
GRAFANA_TOKEN=... \
CF_API_TOKEN=... \
CF_ZONE=cloud-acmetocasino.com \
CLOUDFLARE_HOSTNAME=casino.example.com \
  writing/new-book/scripts/chapter-47/bootstrap-casino/apply-real-secondary-host.sh
```

The real apply script creates BAO placeholder paths, a pfSense Unbound internal
host override, the Grafana dashboard, and the Cloudflare SSL for SaaS custom
hostname. It does not write public DNS and does not flush pfSense states.

## Cloudflare-Only Deployment

Plan only:

```bash
writing/new-book/scripts/chapter-47/bootstrap-casino/plan-cloudflare-secondary-host.sh
```

Apply and validate:

```bash
YES_REAL_APPLY=1 \
CF_API_TOKEN=... \
CF_ZONE=cloud-acmetocasino.com \
CLOUDFLARE_HOSTNAME=casino.example.com \
  writing/new-book/scripts/chapter-47/bootstrap-casino/apply-cloudflare-secondary-host.sh
```

The Cloudflare apply creates the custom hostname and returns ownership
verification and certificate validation records in the audit state. It does not
create the operator's public DNS records; those stay as operator-controlled TXT
and CNAME tasks.
