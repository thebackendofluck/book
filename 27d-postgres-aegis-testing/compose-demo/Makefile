.DEFAULT_GOAL := help

COMPOSE := docker compose
PRIMARY := pg-aegis-primary
PGBENCH := pg-aegis-pgbench
RESULTS := ./results

.PHONY: help up down logs psql status init bench-all bench-baseline bench-tls bench-aegis clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Start the demo stack (primary + replica + pgbouncer + pgbench + prom + grafana)
	mkdir -p $(RESULTS)
	$(COMPOSE) up -d
	@echo "Waiting for primary to be healthy..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
	  if $(COMPOSE) exec -T $(PRIMARY) pg_isready -U demo -d casino >/dev/null 2>&1; then \
	    echo "  primary ready"; break; fi; sleep 3; done

down: ## Stop and remove containers (volumes kept)
	$(COMPOSE) down

clean: ## Stop and remove containers + volumes
	$(COMPOSE) down -v

logs: ## Tail primary logs
	$(COMPOSE) logs -f $(PRIMARY)

psql: ## psql into the primary
	$(COMPOSE) exec $(PRIMARY) psql -U demo -d casino

status: ## Show which encryption path the demo is on
	$(COMPOSE) exec -T $(PRIMARY) psql -U demo -d casino -c "SELECT * FROM aegis_demo_status;"

init: up ## Bring up + run schema/seed (idempotent)
	@echo "Schema + seed already applied by /docker-entrypoint-initdb.d on first start."
	@$(MAKE) status

bench-baseline: up ## T01 baseline OLTP on plain table (pgbench)
	$(COMPOSE) exec -T $(PGBENCH) sh /harness/T01_baseline_oltp.sh

bench-tls: up ## T02 TLS overhead (skipped if ssl=off - see compose file)
	$(COMPOSE) exec -T $(PGBENCH) sh /harness/T02_tls_overhead.sh

bench-aegis: up ## T06 pg_aegis vs pgcrypto column-level
	$(COMPOSE) exec -T $(PGBENCH) sh /harness/T06_aegis_vs_pgcrypto.sh

bench-all: bench-baseline bench-aegis ## Run all non-destructive benchmarks
	@echo "Results in $(RESULTS)/"
