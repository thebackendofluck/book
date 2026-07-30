# Fraud Detection System Makefile
# Provides common development and deployment commands

.PHONY: help build up down logs clean test lint format install dev-setup

# Default target
help: ## Show this help message
	@echo "Fraud Detection System - Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# Development environment
dev-setup: install ## Set up development environment
	@echo "Setting up development environment..."
	@pre-commit install
	@echo "Development environment ready!"

install: ## Install Python dependencies
	@echo "Installing Python dependencies..."
	@pip install -r requirements/requirements-ingestion.txt
	@pip install -r requirements/requirements-feature-engineering.txt
	@pip install -r requirements/requirements-model-serving.txt
	@pip install -r requirements/requirements-alerting.txt
	@pip install -r requirements/requirements-compliance.txt
	@echo "Dependencies installed!"

# Docker commands
build: ## Build all Docker images
	@echo "Building Docker images..."
	@docker-compose build
	@echo "Docker images built!"

up: ## Start all services
	@echo "Starting services..."
	@docker-compose up -d
	@echo "Services started! Access points:"
	@echo "  - Data Ingestion API: http://localhost:8080"
	@echo "  - Feature Engineering API: http://localhost:8081"
	@echo "  - Model Serving API: http://localhost:8082"
	@echo "  - Alert Management API: http://localhost:8083"
	@echo "  - Grafana: http://localhost:3000 (admin / \$$GRAFANA_ADMIN_PASSWORD)"
	@echo "  - Prometheus: http://localhost:9090"
	@echo "  - Kibana: http://localhost:5601"
	@echo "  - MailHog: http://localhost:8025"

down: ## Stop all services
	@echo "Stopping services..."
	@docker-compose down
	@echo "Services stopped!"

logs: ## Show logs from all services
	@docker-compose logs -f

logs-%: ## Show logs from specific service (e.g., make logs-kafka)
	@docker-compose logs -f $*

restart: ## Restart all services
	@docker-compose restart

restart-%: ## Restart specific service
	@docker-compose restart $*

# Database commands
db-init: ## Initialize database schema
	@echo "Initializing database..."
	@docker-compose exec postgresql psql -U fraud_user -d fraud_detection -f /docker-entrypoint-initdb.d/01-init-fraud-detection.sql
	@echo "Database initialized!"

db-migrate: ## Run database migrations
	@echo "Running database migrations..."
	@docker-compose exec postgresql psql -U fraud_user -d fraud_detection -f infrastructure/migrations/$(MIGRATION_FILE)
	@echo "Migration completed!"

db-backup: ## Backup database
	@echo "Backing up database..."
	@docker-compose exec postgresql pg_dump -U fraud_user fraud_detection > backup_$(shell date +%Y%m%d_%H%M%S).sql
	@echo "Database backup created!"

db-restore: ## Restore database from backup
	@echo "Restoring database..."
	@docker-compose exec -T postgresql psql -U fraud_user fraud_detection < $(BACKUP_FILE)
	@echo "Database restored!"

# Testing commands
test: ## Run all tests
	@echo "Running tests..."
	@pytest --cov=src --cov-report=html --cov-report=term-missing
	@echo "Tests completed!"

test-%: ## Run tests for specific module
	@echo "Running tests for $*..."
	@pytest src/$*/tests/ -v

test-integration: ## Run integration tests
	@echo "Running integration tests..."
	@pytest tests/integration/ -v --tb=short

test-load: ## Run load tests
	@echo "Running load tests..."
	@locust -f tests/load/locustfile.py --host=http://localhost:8080

# Code quality commands
lint: ## Run linting checks
	@echo "Running linting checks..."
	@flake8 src/ --max-line-length=88 --extend-ignore=E203,W503
	@mypy src/ --ignore-missing-imports
	@black --check --diff src/
	@isort --check-only --diff src/
	@echo "Linting completed!"

format: ## Format code
	@echo "Formatting code..."
	@black src/
	@isort src/
	@echo "Code formatted!"

security-scan: ## Run security scanning
	@echo "Running security scan..."
	@bandit -r src/ -f json -o security_report.json
	@safety check --json > safety_report.json
	@echo "Security scan completed!"

# ML operations
train-models: ## Train ML models
	@echo "Training ML models..."
	@python -m src.ml_models.train
	@echo "Models trained!"

validate-models: ## Validate ML models
	@echo "Validating ML models..."
	@python -m src.ml_models.validate
	@echo "Models validated!"

# Monitoring commands
monitor-health: ## Check service health
	@echo "Checking service health..."
	@curl -s http://localhost:8080/health | jq .
	@curl -s http://localhost:8081/health | jq .
	@curl -s http://localhost:8082/health | jq .
	@curl -s http://localhost:8083/health | jq .

monitor-metrics: ## Show key metrics
	@echo "Fetching key metrics..."
	@curl -s http://localhost:9090/api/v1/query?query=up | jq '.data.result[] | {name: .metric.__name__, value: .value[1]}'

# Data operations
ingest-sample-data: ## Ingest sample data for testing
	@echo "Ingesting sample data..."
	@python scripts/ingest_sample_data.py
	@echo "Sample data ingested!"

generate-test-data: ## Generate test data
	@echo "Generating test data..."
	@python scripts/generate_test_data.py --count 10000
	@echo "Test data generated!"

# Deployment commands
deploy-dev: ## Deploy to development environment
	@echo "Deploying to development..."
	@docker-compose -f docker-compose.dev.yml up -d
	@echo "Development deployment completed!"

deploy-staging: ## Deploy to staging environment
	@echo "Deploying to staging..."
	@kubectl apply -f k8s/staging/
	@kubectl rollout status deployment/fraud-detection -n staging
	@echo "Staging deployment completed!"

deploy-prod: ## Deploy to production environment
	@echo "Deploying to production..."
	@kubectl apply -f k8s/production/
	@kubectl rollout status deployment/fraud-detection -n production
	@echo "Production deployment completed!"

# Cleanup commands
clean: ## Clean up containers and volumes
	@echo "Cleaning up..."
	@docker-compose down -v
	@docker system prune -f
	@rm -rf __pycache__ *.pyc .pytest_cache .coverage htmlcov
	@echo "Cleanup completed!"

clean-all: ## Clean up everything including images
	@echo "Cleaning up everything..."
	@docker-compose down -v --rmi all
	@docker system prune -a -f
	@rm -rf __pycache__ *.pyc .pytest_cache .coverage htmlcov models/ logs/
	@echo "Full cleanup completed!"

# Utility commands
shell-%: ## Open shell in service container
	@docker-compose exec $* /bin/bash

ps: ## Show running containers
	@docker-compose ps

version: ## Show system version
	@echo "Fraud Detection System v1.0.0"

# CI/CD commands
ci-test: ## Run CI tests
	@echo "Running CI tests..."
	@docker build -t fraud-detection-test -f docker/Dockerfile.test .
	@docker run --rm fraud-detection-test pytest

ci-lint: ## Run CI linting
	@echo "Running CI linting..."
	@docker build -t fraud-detection-lint -f docker/Dockerfile.lint .
	@docker run --rm fraud-detection-lint

ci-security: ## Run CI security scan
	@echo "Running CI security scan..."
	@docker build -t fraud-detection-security -f docker/Dockerfile.security .
	@docker run --rm fraud-detection-security

# Documentation
docs-build: ## Build documentation
	@echo "Building documentation..."
	@sphinx-build -b html docs/ docs/_build/html
	@echo "Documentation built!"

docs-serve: ## Serve documentation locally
	@echo "Serving documentation..."
	@cd docs/_build/html && python -m http.server 8000

# Help for specific targets
help-%: ## Show help for specific target
	@grep -E '^$*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'