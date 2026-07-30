# Makefile for YubiHSM 2 FIPS Enterprise Security Infrastructure

.PHONY: help test test-unit test-integration test-security lint format clean install dev-install security-scan

# Default target
help:
	@echo "Available targets:"
	@echo "  test              - Run all tests"
	@echo "  test-unit         - Run unit tests"
	@echo "  test-integration  - Run integration tests"
	@echo "  test-security     - Run security tests"
	@echo "  lint              - Run code linting"
	@echo "  format            - Format code"
	@echo "  clean             - Clean up generated files"
	@echo "  install           - Install production dependencies"
	@echo "  dev-install       - Install development dependencies"
	@echo "  security-scan     - Run security scanning"

# Testing targets
test:
	pytest tests/ -v --cov=. --cov-report=html

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v -m integration

test-security:
	pytest tests/integration/test_security.py -v -m security

# Code quality targets
lint:
	flake8 . --max-line-length=88
	mypy . --ignore-missing-imports
	bandit -r . -x tests/

format:
	black .
	isort .

# Cleanup
clean:
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	rm -rf __pycache__/
	rm -rf *.pyc
	rm -rf terraform/.terraform/
	rm -rf terraform/terraform.tfstate*

# Installation
install:
	pip install -r requirements.txt

dev-install:
	pip install -r requirements-dev.txt
	pre-commit install

# Security scanning
security-scan:
	safety check
	trivy fs .
	bandit -r . -x tests/

# Terraform targets
terraform-init:
	cd terraform && terraform init

terraform-plan:
	cd terraform && terraform plan

terraform-apply:
	cd terraform && terraform apply

terraform-destroy:
	cd terraform && terraform destroy

# Docker targets
docker-build:
	docker build -t yubihsm-infrastructure .

docker-run:
	docker run -it --rm yubihsm-infrastructure

# Deployment targets
deploy-vaultwarden:
	./vaultwarden_yubihsm.sh install
	./vaultwarden_yubihsm.sh start

deploy-yubihsm:
	./yubihsm_connector_init.sh

deploy-nitro:
	./nitro_enclave_init.sh

deploy-postgres:
	./postgre_tde.sh

deploy-app:
	./app_server_init.sh

# Backup and restore
backup:
	./scripts/backup.sh

restore:
	./scripts/restore.sh $(BACKUP_FILE)

# Monitoring
start-monitoring:
	docker-compose -f monitoring/docker-compose.yml up -d

stop-monitoring:
	docker-compose -f monitoring/docker-compose.yml down

# Documentation
docs-build:
	mkdocs build

docs-serve:
	mkdocs serve

# CI/CD
ci: lint test security-scan

# Development environment
dev-setup: dev-install
	@echo "Development environment setup complete"
	@echo "Run 'make test' to run tests"
	@echo "Run 'make lint' to check code quality"