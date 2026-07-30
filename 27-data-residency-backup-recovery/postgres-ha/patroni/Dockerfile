FROM postgres:18-bookworm

LABEL maintainer="egambling-book"
LABEL description="PostgreSQL 16 + Patroni for HA cluster demo"

ENV PATRONI_VERSION=3.3.0
ENV DEBIAN_FRONTEND=noninteractive

# Install Python, pip and runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        libpq-dev \
        curl \
        wget \
        iproute2 \
        procps \
        jq \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# Install Patroni with etcd3 support via venv to avoid pip externally-managed error
RUN python3 -m venv /opt/patroni-venv \
    && /opt/patroni-venv/bin/pip install --no-cache-dir \
        "patroni[etcd3]==${PATRONI_VERSION}" \
        psycopg2-binary \
        python-etcd \
    && ln -s /opt/patroni-venv/bin/patroni /usr/local/bin/patroni \
    && ln -s /opt/patroni-venv/bin/patronictl /usr/local/bin/patronictl

# WAL archive directory
RUN mkdir -p /wal-archive && chown postgres:postgres /wal-archive

# pg_hba.conf template — will be managed by Patroni
COPY pg_hba.conf /etc/postgresql/pg_hba.conf

# Entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Use a root entrypoint shim to fix permissions, then drop to postgres
COPY fix-perms.sh /fix-perms.sh
RUN chmod +x /fix-perms.sh

# Exception to the non-root USER rule, and a narrow one. Docker creates named
# volumes root-owned, so PGDATA has to be chown'd before postgres can open it;
# only uid 0 can do that. The image therefore starts as root, and fix-perms.sh
# immediately hands off with `exec gosu postgres`, so no long-lived process runs
# as root. If your platform can pre-set volume ownership (Kubernetes fsGroup,
# for example), drop this shim and set USER postgres instead.
USER root
EXPOSE 5432 8008

# Patroni's own REST API (not postgres) answers /health once it has a role.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8008/health || exit 1

ENTRYPOINT ["/fix-perms.sh"]
