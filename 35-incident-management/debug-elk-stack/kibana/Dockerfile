ARG ELK_VERSION=8.13.0

# https://www.docker.elastic.co/
FROM docker.elastic.co/kibana/kibana:${ELK_VERSION}

# Add your kibana plugins setup here
# Example: RUN kibana-plugin install <name|url>

# CIS 4.1 - declare the non-root user explicitly so image scanners can verify it.
# The official Kibana image already ships this user; restating it is a no-op at
# runtime but makes the control auditable.
USER 1000

# CIS 4.6 - health check (K8s readiness probes still apply when orchestrated).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:5601/api/status || exit 1
