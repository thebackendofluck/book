.DEFAULT_GOAL := help
SHELL := /bin/bash

TARGET      ?= lab-server
INVENTORY   := inventory/$(TARGET).yml
ANSIBLE_OPT ?= --diff
BAO_TOKEN   ?=
FORCE       ?= no

.PHONY: help provision bootstrap test destroy lint bao-check

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bao-check: ## Verify BAO_TOKEN works and required paths are readable
	@[ -n "$(BAO_TOKEN)" ] || { echo "ERROR: BAO_TOKEN unset. bao login first."; exit 2; }
	@ssh -o BatchMode=yes lab-server \
	  "BAO_ADDR=https://127.0.0.1:8200 BAO_TOKEN=$(BAO_TOKEN) bao kv get -format=json casino/postgres/aegis/dek 2>&1 | head -3" \
	  || { echo "ERROR: cannot read casino/postgres/aegis/dek"; exit 3; }
	@echo "bao-check OK"

lint: ## Lint shell scripts, ansible, yaml
	@shellcheck libvirt/create-cluster.sh proxmox/create-cluster.sh tests/*.sh
	@ansible-lint ansible/site.yml || true
	@echo "lint OK"

provision: bao-check ## Create VMs on TARGET (lab-server libvirt or secondary-host Proxmox)
	@case "$(TARGET)" in \
	  lab-server) bash libvirt/create-cluster.sh ;; \
	  secondary-host)    bash proxmox/create-cluster.sh ;; \
	  *) echo "unknown TARGET=$(TARGET)"; exit 2 ;; \
	esac

bootstrap: bao-check ## Run Ansible site.yml against the provisioned cluster
	ansible-playbook -i $(INVENTORY) ansible/site.yml $(ANSIBLE_OPT) \
	  --extra-vars "bao_token=$(BAO_TOKEN)"

test: ## Run full T01..T12 matrix against the cluster
	bash tests/run-prod-matrix.sh $(TARGET)

test-rto: ## Only T11 — backup + destroy + restore + measure RTO
	bash tests/T11_backup_restore_rto.sh $(TARGET)

test-chaos: ## Only T12 — random leader kill every 10 min (60 min total)
	bash tests/T12_chaos_leader_kill.sh $(TARGET)

destroy: ## Destroy the VMs (refuses without FORCE=yes on prod-like targets)
	@if [ "$(TARGET)" = "secondary-host" ] && [ "$(FORCE)" != "yes" ]; then \
	  echo "refusing: pass FORCE=yes to destroy secondary-host VMs"; exit 4; \
	fi
	@case "$(TARGET)" in \
	  lab-server) bash libvirt/create-cluster.sh --destroy ;; \
	  secondary-host)    bash proxmox/create-cluster.sh --destroy ;; \
	esac
