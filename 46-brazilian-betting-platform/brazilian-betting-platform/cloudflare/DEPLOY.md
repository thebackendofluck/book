# Deploying the bet-brazil edge workers

This is the operational runbook for the reference edge adapters. It covers how
to request and generate the secrets, test locally and in homologation, deploy
in a dependency-safe order, validate, and promote to production. These Workers
are teaching examples; adapt the account, routes, and provider steps to your own
environment.

## 1. Secrets: request, generate, provision

Every secret and its provenance is listed in [`.dev.vars.example`](.dev.vars.example).
Three kinds:

- **Generated** internal HMAC secrets. Create each with a CSPRNG and store it in
  your secret manager:
  ```bash
  openssl rand -hex 32
  ```
  These are `GATEWAY_INTERNAL_HMAC_SECRET`, `SIGAP_COMPLIANCE_HMAC_SECRET`,
  `WALLET_INTERNAL_HMAC_SECRET`, and `AWS_CORE_HMAC_SECRET`.
  `GATEWAY_INTERNAL_HMAC_SECRET` must be the **same value** on `api-gateway` and
  `pix-webhook`, because one signs and the other verifies.

- **Provider** credentials, requested from the contracted PSP under the service
  agreement. `PIX_HMAC_SECRET` is the inbound webhook-validation secret; it is
  **not** the outbound credential. `PIX_PSP_API_KEY` is the outbound bearer for
  PSP API calls. Keep them distinct so a leak or rotation in one direction does
  not compromise the other. In homologation, use the provider's sandbox values.

- **Platform / regulator** credentials: `JWT_SECRET` (your identity service),
  `SIGAP_BEARER_TOKEN` (the SIGAP submission credential from the regulator's
  homologation, then production, environment).

Provision each secret per worker (never in a `.toml`, never in git):

```bash
# generated internal secrets
GW=$(openssl rand -hex 32)
printf '%s' "$GW" | wrangler secret put GATEWAY_INTERNAL_HMAC_SECRET --config wrangler.toml
printf '%s' "$GW" | wrangler secret put GATEWAY_INTERNAL_HMAC_SECRET --config wrangler.pix-webhook.toml
printf '%s' "$(openssl rand -hex 32)" | wrangler secret put SIGAP_COMPLIANCE_HMAC_SECRET --config wrangler.sigap-reporter.toml
printf '%s' "$(openssl rand -hex 32)" | wrangler secret put WALLET_INTERNAL_HMAC_SECRET --config wrangler.wallet.toml

# provider credentials (sandbox values in homologation)
wrangler secret put PIX_PSP_API_KEY --config wrangler.pix-webhook.toml
wrangler secret put PIX_PSP_API_KEY --config wrangler.wallet.toml
```

Setting a secret does not change the running code, so it is safe to provision
secrets on the currently deployed (old) workers before shipping the new code.

## 2. Test locally

```bash
cp .dev.vars.example .dev.vars    # fill in sandbox/dummy values
npm install
npm run type-check                # tsc --noEmit
npm test                          # vitest: unit + auth-gate tests (108 tests)
```

The suite asserts the security invariants directly: each protected route accepts
a validly signed request and returns 401 for a missing or invalid signature, the
token refresh rejects a revoked session, and withdrawals reject a PIX key that
does not match the registered deposit key. Do not deploy on a red suite.

## 3. Deploy (dependency-safe order)

Build first without shipping, to catch bundling or config errors:

```bash
for c in wrangler.toml wrangler.pix-webhook.toml wrangler.odds-feed.toml \
         wrangler.sigap-reporter.toml wrangler.wallet.toml wrangler.session-manager.toml; do
  wrangler deploy --dry-run --config "$c" >/dev/null && echo "ok: $c"
done
```

Then deploy. The guarded script provisions nothing but refuses to ship a worker
whose required secrets are missing, and it deploys in the order that keeps the
deposit path intact (gateway starts signing before pix-webhook starts requiring):

```bash
bash scripts/deploy.sh
```

If you deploy by hand, keep this order: `api-gateway` first, then `pix-webhook`,
then the rest. Deploying `pix-webhook` before `api-gateway` would briefly reject
legitimate deposits, because the gateway would not yet be signing its calls.

Upstream callers must be updated to sign too: the SIGAP compliance pipeline must
send `SIGAP_COMPLIANCE_HMAC_SECRET`-signed requests to `/batches`, and the odds
publisher must sign `/odds/suspend` the same way it already signs `/odds/refresh`.
Roll those callers out with, or before, these Workers.

## 4. Validate

```bash
bash scripts/smoke-test.sh
```

It confirms the public site still serves, `sports-api` now carries HSTS and
`X-Content-Type-Options`, and no backend worker is reachable on `*.workers.dev`.
The script exits non-zero on any failure, so it can gate a CI promotion.

## 5. Promote to production

Ship to homologation first, run steps 2 to 4 against the provider and regulator
homologation environments, then repeat for production. Cloudflare environment
targets are available via `wrangler deploy --env production` where a `[env.*]`
block is defined.

## Rollback

```bash
wrangler deployments list --config wrangler.<worker>.toml   # find the prior version
wrangler rollback --config wrangler.<worker>.toml           # revert to it
```

Rolling a worker back to a pre-hardening version reopens its unauthenticated
route, so treat rollback as an incident action and re-deploy the fix promptly.
