# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# modules/onpremise/main.tf
# Chapter 24: On-premises provisioning via SSH null_resource
#
# Provisions:
#   - Threat list consolidation script (3x daily cron)
#   - ip-detection FastAPI service (Python 3.11+)
#   - systemd unit files for both services
#   - Redis data structure initialisation
#   - Log rotation configuration
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "host" { type = string }
variable "user" { type = string }
variable "ssh_key_path" { type = string }
variable "ssh_port" { type = number }
variable "app_dir" { type = string }
variable "redis_url" { type = string }
variable "maxmind_db_path" { type = string }
variable "maxmind_city_db_path" { type = string }
variable "kyc_service_url" { type = string }
variable "threat_list_schedule" { type = string }
variable "environment" { type = string }
variable "fraud_block_threshold" { type = number }
variable "fraud_review_threshold" { type = number }
variable "source_dir" { type = string }
variable "threat_list_source_dir" { type = string }

variable "rate_limit_thresholds" {
  type = object({
    requests_per_minute = number
    requests_per_5min   = number
    requests_per_hour   = number
  })
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  ssh_opts = "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p ${var.ssh_port} -i ${var.ssh_key_path}"
  scp_base = "scp ${local.ssh_opts}"
}

# =============================================================================
# Step 1: Directory structure & system dependencies
# =============================================================================

resource "null_resource" "system_setup" {

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "30s"
  }

  provisioner "remote-exec" {
    inline = [
      # Verify Python 3.11+ is available.
      "python3 --version | grep -E 'Python 3\\.(11|12|13)' || (echo 'ERROR: Python 3.11+ required' && exit 1)",

      # Install system dependencies if missing.
      "which redis-cli > /dev/null 2>&1 || sudo apt-get install -y redis-tools",

      # Create application directory structure.
      "sudo mkdir -p ${var.app_dir}/{logs,threat-lists/{output,raw},venv}",
      "sudo chown -R ${var.user}:${var.user} ${var.app_dir}",
      "sudo chmod 750 ${var.app_dir}",

      # Create config directory.
      "sudo mkdir -p /etc/ip-detection",
      "sudo chown root:${var.user} /etc/ip-detection",
      "sudo chmod 750 /etc/ip-detection",

      # Create log directory with rotation-friendly permissions.
      "sudo mkdir -p /var/log/ip-detection",
      "sudo chown ${var.user}:adm /var/log/ip-detection",
      "sudo chmod 750 /var/log/ip-detection",
    ]
  }

  triggers = {
    app_dir = var.app_dir
    user    = var.user
  }
}

# =============================================================================
# Step 2: Deploy application source files via SCP
# =============================================================================

resource "null_resource" "deploy_app_source" {
  depends_on = [null_resource.system_setup]

  # Re-deploy whenever source files change (hash the directory contents).
  triggers = {
    source_hash = sha256(join("", [
      for f in fileset(var.source_dir, "**/*.py") :
      filesha256("${var.source_dir}/${f}")
    ]))
  }

  provisioner "local-exec" {
    command = "${local.scp_base} -r ${var.source_dir}/*.py ${var.user}@${var.host}:${var.app_dir}/"
  }

  provisioner "local-exec" {
    command = "${local.scp_base} ${var.source_dir}/requirements.txt ${var.user}@${var.host}:${var.app_dir}/requirements.txt"
  }
}

# =============================================================================
# Step 3: Deploy threat-lists scripts
# =============================================================================

resource "null_resource" "deploy_threat_lists" {
  depends_on = [null_resource.system_setup]

  triggers = {
    source_hash = sha256(join("", [
      for f in fileset(var.threat_list_source_dir, "**/*.py") :
      filesha256("${var.threat_list_source_dir}/${f}")
    ]))
  }

  provisioner "local-exec" {
    command = "mkdir -p /tmp/threat-lists-deploy && cp -r ${var.threat_list_source_dir}/. /tmp/threat-lists-deploy/"
  }

  provisioner "local-exec" {
    command = "${local.scp_base} -r /tmp/threat-lists-deploy/. ${var.user}@${var.host}:${var.app_dir}/threat-lists/"
  }
}

# =============================================================================
# Step 4: Python virtual environment + pip dependencies
# =============================================================================

resource "null_resource" "python_venv" {
  depends_on = [null_resource.deploy_app_source]

  triggers = {
    requirements_hash = fileexists("${var.source_dir}/requirements.txt") ? filesha256("${var.source_dir}/requirements.txt") : "none"
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "120s"
  }

  provisioner "remote-exec" {
    inline = [
      "python3 -m venv ${var.app_dir}/venv",
      "${var.app_dir}/venv/bin/pip install --upgrade pip --quiet",
      "${var.app_dir}/venv/bin/pip install -r ${var.app_dir}/requirements.txt --quiet",
      "echo 'Python dependencies installed successfully'",
    ]
  }
}

# =============================================================================
# Step 5: Write environment configuration file
# =============================================================================

resource "null_resource" "write_env_file" {
  depends_on = [null_resource.system_setup]

  triggers = {
    env_hash = sha256(join("", [
      var.redis_url,
      var.maxmind_db_path,
      var.environment,
      tostring(var.fraud_block_threshold),
      tostring(var.fraud_review_threshold),
    ]))
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "30s"
  }

  provisioner "remote-exec" {
    inline = [
      # Write env file using heredoc; no local file with secrets.
      "sudo tee /etc/ip-detection/env > /dev/null <<'ENVEOF'",
      "# ip-detection environment — written by Terraform on ${timestamp()}",
      "REDIS_URL=${var.redis_url}",
      "MAXMIND_DB_PATH=${var.maxmind_db_path}",
      "MAXMIND_CITY_DB_PATH=${var.maxmind_city_db_path}",
      "KYC_SERVICE_URL=${var.kyc_service_url}",
      "PIPELINE_ENV=${var.environment}",
      "FRAUD_SCORE_THRESHOLD=${var.fraud_block_threshold}",
      "FRAUD_SCORE_REVIEW=${var.fraud_review_threshold}",
      "RATE_LIMIT_PER_MINUTE=${var.rate_limit_thresholds.requests_per_minute}",
      "RATE_LIMIT_PER_5MIN=${var.rate_limit_thresholds.requests_per_5min}",
      "RATE_LIMIT_PER_HOUR=${var.rate_limit_thresholds.requests_per_hour}",
      "LOG_FORMAT=json",
      "ENVEOF",
      "sudo chmod 640 /etc/ip-detection/env",
      "sudo chown root:${var.user} /etc/ip-detection/env",
    ]
  }
}

# =============================================================================
# Step 6: Install systemd unit files
# =============================================================================

resource "null_resource" "systemd_ip_detection" {
  depends_on = [null_resource.python_venv, null_resource.write_env_file]

  triggers = {
    service_definition = sha256(join("", [
      var.app_dir,
      var.user,
      var.environment,
    ]))
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "30s"
  }

  provisioner "remote-exec" {
    inline = [
      "sudo tee /etc/systemd/system/ip-detection.service > /dev/null <<'SVCEOF'",
      "[Unit]",
      "Description=iGaming IP Detection FastAPI Service (${var.environment})",
      "Documentation=https://github.com/thebackendofluck/book/tree/main/24-security-compliance/ip-detection",
      "After=network.target redis.service",
      "Wants=redis.service",
      "",
      "[Service]",
      "Type=simple",
      "User=${var.user}",
      "Group=${var.user}",
      "WorkingDirectory=${var.app_dir}",
      "EnvironmentFile=/etc/ip-detection/env",
      "ExecStart=${var.app_dir}/venv/bin/uvicorn ip_detection_pipeline:app --host 0.0.0.0 --port 8091 --workers 4 --log-level info --no-access-log",
      "ExecReload=/bin/kill -HUP $MAINPID",
      "Restart=on-failure",
      "RestartSec=5",
      "StartLimitIntervalSec=60",
      "StartLimitBurst=3",
      "NoNewPrivileges=true",
      "ProtectSystem=strict",
      "ProtectHome=read-only",
      "ReadWritePaths=${var.app_dir}/logs",
      "PrivateTmp=true",
      "CapabilityBoundingSet=",
      "LockPersonality=true",
      "RestrictRealtime=true",
      "RestrictSUIDSGID=true",
      "SystemCallFilter=@system-service",
      "SystemCallErrorNumber=EPERM",
      "",
      "[Install]",
      "WantedBy=multi-user.target",
      "SVCEOF",

      # Threat-list refresh service (triggered by timer, not daemon).
      "sudo tee /etc/systemd/system/threat-refresh.service > /dev/null <<'SVCEOF'",
      "[Unit]",
      "Description=iGaming Threat List Refresh (one-shot)",
      "After=network-online.target",
      "Wants=network-online.target",
      "",
      "[Service]",
      "Type=oneshot",
      "User=${var.user}",
      "Group=${var.user}",
      "WorkingDirectory=${var.app_dir}",
      "EnvironmentFile=/etc/ip-detection/env",
      "ExecStart=${var.app_dir}/venv/bin/python3 ${var.app_dir}/threat-lists/consolidate_threat_lists.py",
      "StandardOutput=journal",
      "StandardError=journal",
      "TimeoutStartSec=300",
      "NoNewPrivileges=true",
      "ProtectSystem=strict",
      "ProtectHome=read-only",
      "ReadWritePaths=${var.app_dir}/threat-lists/output",
      "PrivateTmp=true",
      "SVCEOF",

      "sudo systemctl daemon-reload",
      "sudo systemctl enable ip-detection.service",
      "sudo systemctl restart ip-detection.service",
      "sleep 3",
      "sudo systemctl is-active ip-detection.service || (sudo journalctl -u ip-detection -n 50 && exit 1)",
    ]
  }
}

# =============================================================================
# Step 7: Configure cron for threat list refresh (3x daily)
# =============================================================================

resource "null_resource" "threat_list_cron" {
  depends_on = [null_resource.deploy_threat_lists, null_resource.python_venv]

  triggers = {
    schedule = var.threat_list_schedule
    app_dir  = var.app_dir
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "30s"
  }

  provisioner "remote-exec" {
    inline = [
      # Remove any existing ip-detection cron entries to avoid duplicates.
      "crontab -l 2>/dev/null | grep -v 'consolidate_threat_lists\\|# ip-detection' > /tmp/crontab_clean || true",

      # Append the new cron schedule.
      # threat_list_schedule is a standard 5-field cron expression.
      "echo '# ip-detection threat list refresh — managed by Terraform' >> /tmp/crontab_clean",
      "echo '${var.threat_list_schedule} ${var.app_dir}/venv/bin/python3 ${var.app_dir}/threat-lists/consolidate_threat_lists.py >> /var/log/ip-detection/threat-refresh.log 2>&1' >> /tmp/crontab_clean",

      # Install the new crontab.
      "crontab /tmp/crontab_clean",
      "rm /tmp/crontab_clean",

      # Verify.
      "crontab -l | grep consolidate_threat_lists",
      "echo 'Cron schedule installed: ${var.threat_list_schedule}'",
    ]
  }
}

# =============================================================================
# Step 8: Configure log rotation
# =============================================================================

resource "null_resource" "logrotate" {
  depends_on = [null_resource.system_setup]

  triggers = {
    app_dir = var.app_dir
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "30s"
  }

  provisioner "remote-exec" {
    inline = [
      "sudo tee /etc/logrotate.d/ip-detection > /dev/null <<'LOGEOF'",
      "/var/log/ip-detection/*.log {",
      "    daily",
      "    missingok",
      "    rotate 30",
      "    compress",
      "    delaycompress",
      "    notifempty",
      "    create 640 ${var.user} adm",
      "    sharedscripts",
      "    postrotate",
      "        systemctl kill --signal=HUP ip-detection.service > /dev/null 2>&1 || true",
      "    endscript",
      "}",
      "LOGEOF",

      "sudo chmod 644 /etc/logrotate.d/ip-detection",
    ]
  }
}

# =============================================================================
# Step 9: Initialise Redis data structures
# =============================================================================

resource "null_resource" "redis_init" {
  depends_on = [null_resource.system_setup]

  triggers = {
    redis_url   = var.redis_url
    environment = var.environment
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "30s"
  }

  provisioner "remote-exec" {
    inline = [
      # Parse Redis URL to extract connection params.
      "REDIS_HOST=$(echo '${var.redis_url}' | sed 's|redis://||' | cut -d: -f1)",
      "REDIS_PORT=$(echo '${var.redis_url}' | sed 's|redis://||' | cut -d: -f2 | cut -d/ -f1)",
      "REDIS_DB=$(echo '${var.redis_url}' | grep -oP '(?<=/)\\d+$' || echo '0')",

      # Verify Redis connectivity.
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB PING | grep -q PONG || (echo 'ERROR: Cannot connect to Redis' && exit 1)",

      # Initialise sorted sets for velocity tracking.
      # These are pre-created as empty sets to ensure correct type on first write.
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB DEL ip:init:seed > /dev/null",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB ZADD ip:init:seed 0 'init' > /dev/null",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB DEL ip:init:seed > /dev/null",

      # Set global config keys.
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB SET config:fraud_block_threshold ${var.fraud_block_threshold} EX 86400",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB SET config:fraud_review_threshold ${var.fraud_review_threshold} EX 86400",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB SET config:environment '${var.environment}' EX 86400",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB SET config:rate_limit_per_minute ${var.rate_limit_thresholds.requests_per_minute} EX 86400",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB SET config:rate_limit_per_5min ${var.rate_limit_thresholds.requests_per_5min} EX 86400",
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB SET config:rate_limit_per_hour ${var.rate_limit_thresholds.requests_per_hour} EX 86400",

      # Verify keys were written.
      "redis-cli -h $REDIS_HOST -p $REDIS_PORT -n $REDIS_DB GET config:environment",
      "echo 'Redis initialisation complete'",
    ]
  }
}

# =============================================================================
# Step 10: Validate deployment — smoke test
# =============================================================================

resource "null_resource" "smoke_test" {
  depends_on = [null_resource.systemd_ip_detection, null_resource.redis_init]

  triggers = {
    # Re-run smoke test on every apply to catch regressions.
    always_run = timestamp()
  }

  connection {
    type        = "ssh"
    host        = var.host
    user        = var.user
    private_key = file(var.ssh_key_path)
    port        = var.ssh_port
    timeout     = "60s"
  }

  provisioner "remote-exec" {
    inline = [
      # Wait up to 15 seconds for the service to start.
      "for i in $(seq 1 15); do",
      "  curl -sf http://localhost:8091/health > /dev/null 2>&1 && break",
      "  sleep 1",
      "done",

      # Verify health endpoint responds.
      "curl -sf http://localhost:8091/health | grep -q 'ok\\|healthy\\|status' || (echo 'ERROR: Health check failed' && exit 1)",

      # Verify systemd service is active.
      "systemctl is-active ip-detection.service",

      "echo '=== Deployment smoke test PASSED ==='",
      "echo 'Service: ip-detection @ ${var.host}:8091'",
      "echo 'Environment: ${var.environment}'",
    ]
  }
}

# =============================================================================
# Outputs
# =============================================================================

output "service_endpoint" {
  value       = "http://${var.host}:8091"
  description = "Internal endpoint of the ip-detection FastAPI service"
}

output "health_check_command" {
  value       = "curl -sf http://${var.host}:8091/health"
  description = "Command to check service health from within the network"
}

output "cron_schedule" {
  value       = var.threat_list_schedule
  description = "Installed cron schedule for threat list refresh"
}
