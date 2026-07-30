# Makefile — casino platform top-level
# Usage: make dev | make prod | make test | make debug SERVICE=wallet-service

REGISTRY := registry.ops-host.local:5000
SERVICES  := player-service wallet-service game-engine bonus-engine compliance-reporter
GIT_SHA   := $(shell git rev-parse --short HEAD)

.PHONY: dev prod test debug build-all push-all clean

# ─── Developer targets ──────────────────────────────────────────────────────────

dev:
	@echo "Starting development environment with hot reload..."
	docker compose -f docker-compose.dev.yml up --build

dev-down:
	docker compose -f docker-compose.dev.yml down -v

dev-logs:
	docker compose -f docker-compose.dev.yml logs -f

dev-rebuild:
	@echo "Rebuilding all dev images (use when requirements.txt changes)..."
	docker compose -f docker-compose.dev.yml build --no-cache

# ─── Production targets ─────────────────────────────────────────────────────────

prod-build:
	@echo "Building production images tagged with git SHA $(GIT_SHA)..."
	@for svc in $(SERVICES); do \
		docker build \
			-t $(REGISTRY)/casino/$${svc}:$(GIT_SHA) \
			-t $(REGISTRY)/casino/$${svc}:latest \
			-f ./$${svc}/Dockerfile \
			./$${svc}; \
	done

prod-push:
	@echo "Pushing to internal registry $(REGISTRY)..."
	@for svc in $(SERVICES); do \
		docker push $(REGISTRY)/casino/$${svc}:$(GIT_SHA); \
		docker push $(REGISTRY)/casino/$${svc}:latest; \
	done

# ─── Test targets ───────────────────────────────────────────────────────────────

test:
	@echo "Running unit tests in dev containers..."
	@for svc in $(SERVICES); do \
		docker compose -f docker-compose.dev.yml exec $${svc} \
			pytest tests/unit/ -x -q --tb=short; \
	done

test-integration:
	@echo "Running integration tests against docker-compose environment..."
	docker compose -f docker-compose.dev.yml exec player-service \
		pytest tests/integration/ -x -q --tb=long -v

# ─── Debugging targets ──────────────────────────────────────────────────────────

debug:
ifndef SERVICE
	$(error SERVICE is not set. Usage: make debug SERVICE=wallet-service)
endif
	@echo "Starting $(SERVICE) with debugpy — attach VS Code to localhost:5678"
	docker compose -f docker-compose.dev.yml stop $(SERVICE)
	docker compose -f docker-compose.dev.yml run --rm \
		-p 5678:5678 \
		$(SERVICE)

# Tilt targets
tilt-up:
	KUBECONFIG=~/.kube/ops-host-config tilt up

tilt-down:
	KUBECONFIG=~/.kube/ops-host-config tilt down

clean:
	docker compose -f docker-compose.dev.yml down -v --rmi local
	docker system prune -f
