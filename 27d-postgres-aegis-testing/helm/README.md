# postgres-aegis Helm chart

Deploys a layered-encryption PostgreSQL cluster for iGaming workloads:

- 1 writer (Patroni leader) + 1 hot standby (automated failover).
- 10 read replicas behind PgBouncer.
- cert-manager Certificate → mTLS between all pods.
- external-secrets-operator → OpenBao-backed DEK for `pg_aegis` column AEAD (falls back to `pgcrypto` when the `pg_aegis` extension is not present in the image).
- pgbackrest CronJob → Wasabi-compatible S3, AES-256-CBC encrypted bundle.
- NetworkPolicy that only lets PgBouncer reach Patroni and only lets the namespaces in `values.yaml` reach PgBouncer.

## Honesty disclaimers (read before shipping)

1. **"2 writers" in `values.yaml` means pod count — it is NOT active-active.** True multi-primary needs pgEdge BDR or similar. Patroni elects one leader; the second pod is a hot standby promoted on failover. This is clearly stated in chapter 27d.
2. **`pg_aegis` is expected to be supplied by the image.** When `image.aegisExtensionImage` is empty, the post-init SQL silently falls back to `pgcrypto`. The `init` log prints which path was taken — grep `pg_aegis enabled` to confirm.
3. **TLS material comes from cert-manager.** If the Issuer doesn't exist in the release namespace, the chart will render but pods will crash-loop until the Certificate is ready. Install cert-manager + the Issuer first.
4. **OpenBao paths are assumed.** Bootstrap:
   ```bash
   bao kv put casino/postgres/aegis/dek         value=$(openssl rand -hex 16)
   bao kv put casino/postgres/aegis/super       password=$(openssl rand -hex 24)
   bao kv put casino/postgres/aegis/replicator  password=$(openssl rand -hex 24)
   ```

## Install

```bash
helm lint .
helm template postgres-aegis . --namespace casino-db | kubectl apply --dry-run=client -f -
helm install postgres-aegis . --namespace casino-db --create-namespace
```

## Values

Key knobs (full list in `values.yaml`):

| Key | Default | Note |
|---|---|---|
| `writers.replicaCount` | 2 | 1 leader + N standbys |
| `readers.replicaCount` | 10 | Horizontal read scaling |
| `image.aegisExtensionImage` | `""` | Leave empty → honest pgcrypto fallback |
| `aegis.dekSecretPath` | `casino/postgres/aegis/dek` | OpenBao key |
| `pgbouncer.poolMode` | `transaction` | Recommended for iGaming wallet workloads |
| `backup.s3Endpoint` | Wasabi EU | Swap to AWS S3 in cloud parity mode |

## Related artifacts (this repo)

- `../compose-demo/` — laptop reproducible demo.
- `../tests-laptop/` — T01–T10 benchmark harnesses.
- `../terraform/rds-postgres-aegis/` — AWS/LocalStack parity module.
- `../../../chapters/27d-postgres-aegis-testing.md` — book chapter.
- `../../../appendices/Appendix_H_LocalStack_vs_AWS_Gotchas.md` — LocalStack caveats.
