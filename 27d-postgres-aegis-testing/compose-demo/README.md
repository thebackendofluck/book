# postgres-aegis-demo

Reader-reproducible demo for book chapter 27d.

## Prereqs

- Docker Desktop or Docker Engine 24+
- `make`
- ~4 GB RAM
- 20 minutes

## Quickstart (5 commands)

```bash
cd infrastructure/compose/postgres-aegis-demo
make up          # boot primary + replica + pgbouncer + pgbench + prometheus + grafana
make status      # shows whether pg_aegis is real or pgcrypto fallback
make bench-baseline
make bench-aegis
make clean       # tear down
```

Results land in `./results/` as CSV. Grafana at http://localhost:3030
(anon; nothing wired by default — left as exercise).

## What is honest about this demo

- **pgcrypto is real.** Numbers for the `pgcrypto` column are real crypto
  workload (AES-256-CBC via OpenSSL inside Postgres).
- **pg_aegis may be a stub.** If the image doesn't have `pg_aegis.so`,
  `aegis_demo_encrypt()` transparently falls back to pgcrypto. You will
  then see roughly identical TPS for the "aegis" table, not the 4.3x win
  reported in thebackendofluck.com. `make status` tells you which mode.
- **Replication is real.** `pg-replica` is a real streaming hot standby
  initialized via `pg_basebackup`.
- **TLS is disabled by default** to keep the demo quickstart simple.
  T02 (TLS overhead) is skipped unless you flip `ssl=on` in the compose file
  and mount real certs in `./tls/` — instructions below.

## Enabling real TLS

```bash
mkdir -p tls
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout tls/server.key -out tls/server.crt \
  -days 365 -subj "/CN=pg-primary"
chmod 600 tls/server.key
# Edit docker-compose.yaml: change `ssl=off` to `ssl=on` and add
#   -c ssl_cert_file=/etc/postgresql/tls/server.crt
#   -c ssl_key_file=/etc/postgresql/tls/server.key
make clean && make up
make bench-tls
```

## Enabling real `pg_aegis`

`pg_aegis` is not yet shipped as a public binary extension (as of 2026-04).
The thebackendofluck.com article references a custom build. Two options:

1. **Build from source** (community repo expected to be published 2026-Q2).
2. **Use an image that bundles it** — set `postgres-aegis` image in
   `docker-compose.yaml`:
   ```yaml
   image: your-registry/postgres:16.4-aegis
   ```

Until then, the demo runs in honest pgcrypto fallback mode.

## Related artifacts

- Harness scripts: `../tests-laptop/`
- Book chapter: `../../../chapters/27d-postgres-aegis-testing.md`
- LocalStack gotchas: `../../../appendices/Appendix_H_LocalStack_vs_AWS_Gotchas.md`
- Production Helm chart: `../helm/`
- AWS Terraform module: `../terraform/rds-postgres-aegis/`
