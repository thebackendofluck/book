#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# dependency-audit.sh
# Audit all dependencies across all supported package managers for known
# vulnerabilities. Produces a consolidated JSON report and per-ecosystem
# detail files. Suitable for scheduled CI runs and pre-release gates.
#
# Supported ecosystems:
#   npm (package-lock.json)    -- npm audit
#   Python (requirements*.txt, pyproject.toml) -- pip-audit
#   Go (go.sum)                -- govulncheck
#   Ruby (Gemfile.lock)        -- bundle audit
#   Java/Maven (pom.xml)       -- dependency-check (OWASP)
#   Rust (Cargo.lock)          -- cargo audit
#   Container images           -- trivy / grype
#
# Usage:
#   ./dependency-audit.sh [--severity <critical|high|medium|low>]
#                         [--report-dir <path>]
#                         [--ecosystem <npm|python|go|ruby|java|rust|containers|all>]
#                         [--fail-on-findings]
#                         [--image <image:tag>]
#
# Exit codes:
#   0  Audit complete, no findings at or above severity threshold
#   1  Findings found at or above severity threshold (when --fail-on-findings)
#   2  Audit tool not available

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SEVERITY="${SEVERITY:-high}"          # critical | high | medium | low
REPORT_DIR="${REPORT_DIR:-./audit-reports}"
ECOSYSTEM="${ECOSYSTEM:-all}"
FAIL_ON_FINDINGS="${FAIL_ON_FINDINGS:-false}"
IMAGE_TAG="${IMAGE_TAG:-}"
LOG_PREFIX="[dep-audit]"

declare -A ECOSYSTEM_RESULTS
TOTAL_VULNS=0
CRITICAL_COUNT=0
HIGH_COUNT=0
MEDIUM_COUNT=0
LOW_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()     { echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] $*"; }
log_ok()  { echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] ✓  $*"; }
log_err() { echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] ✗  $*" >&2; }
log_warn(){ echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] ⚠  $*"; }
log_skip(){ echo "${LOG_PREFIX} [$(date -u +%H:%M:%SZ)] –  $*"; }

require_tool() {
    command -v "$1" >/dev/null 2>&1
}

should_run() {
    local eco="$1"
    [[ "${ECOSYSTEM}" == "all" ]] || [[ "${ECOSYSTEM}" == "${eco}" ]]
}

severity_meets_threshold() {
    local sev="$1"
    case "${SEVERITY}" in
        critical) [[ "${sev}" == "critical" ]] ;;
        high)     [[ "${sev}" =~ ^(critical|high)$ ]] ;;
        medium)   [[ "${sev}" =~ ^(critical|high|medium)$ ]] ;;
        low)      true ;;
        *)        false ;;
    esac
}

mkdir -p "${REPORT_DIR}"

# ---------------------------------------------------------------------------
# npm audit
# ---------------------------------------------------------------------------
audit_npm() {
    log "Auditing npm dependencies..."
    local report_file="${REPORT_DIR}/npm-audit.json"

    if ! require_tool npm; then
        log_skip "npm not found"
        ECOSYSTEM_RESULTS["npm"]="SKIP"
        return
    fi

    # Find all package-lock.json files (monorepo support)
    local npm_findings=0
    find . -name "package-lock.json" \
         -not -path "*/node_modules/*" \
         -not -path "*/.git/*" | while IFS= read -r lockfile; do
        local pkg_dir
        pkg_dir="$(dirname "${lockfile}")"
        log "  Auditing ${pkg_dir}..."

        npm audit \
            --prefix "${pkg_dir}" \
            --json 2>/dev/null \
            > "${REPORT_DIR}/npm-audit-$(echo "${pkg_dir}" | tr '/' '_').json" || true
    done

    # Aggregate results
    python3 - <<PYEOF
import json, glob, os

all_vulns = {"critical": [], "high": [], "medium": [], "low": [], "info": []}
audit_files = glob.glob("${REPORT_DIR}/npm-audit-*.json")

for audit_file in audit_files:
    try:
        with open(audit_file) as f:
            data = json.load(f)
        pkg_dir = os.path.basename(audit_file)
        for pkg_name, vuln in data.get("vulnerabilities", {}).items():
            severity = vuln.get("severity", "info").lower()
            all_vulns.setdefault(severity, []).append({
                "ecosystem": "npm",
                "package": pkg_name,
                "severity": severity,
                "title": vuln.get("title", vuln.get("name", "N/A")),
                "url": vuln.get("url", ""),
                "source": pkg_dir,
                "via": [str(v) if isinstance(v, str) else v.get("title", "")
                        for v in vuln.get("via", [])[:3]],
            })
    except (json.JSONDecodeError, KeyError):
        pass

summary = {k: len(v) for k, v in all_vulns.items()}
print(f"npm: critical={summary.get('critical',0)}, high={summary.get('high',0)}, "
      f"medium={summary.get('medium',0)}, low={summary.get('low',0)}")

# iGaming-specific: highlight payment-related packages
payment_pkgs = ["stripe", "adyen", "paypal", "braintree", "square", "checkout"]
for sev, vulns in all_vulns.items():
    for v in vulns:
        if any(p in v["package"].lower() for p in payment_pkgs):
            print(f"  ⚠ PAYMENT PACKAGE {sev.upper()}: {v['package']} -- {v['title']}")

with open("${report_file}", "w") as f:
    json.dump({
        "ecosystem": "npm",
        "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
        "summary": summary,
        "vulnerabilities": {k: v for k, v in all_vulns.items() if v}
    }, f, indent=2)
PYEOF

    ECOSYSTEM_RESULTS["npm"]="DONE"
    log_ok "npm audit complete -- report: ${report_file}"
}

# ---------------------------------------------------------------------------
# pip-audit
# ---------------------------------------------------------------------------
audit_python() {
    log "Auditing Python dependencies..."
    local report_file="${REPORT_DIR}/pip-audit.json"

    if ! require_tool pip-audit; then
        if require_tool pip3; then
            log "Installing pip-audit..."
            pip3 install pip-audit --quiet
        else
            log_skip "pip-audit and pip3 not found"
            ECOSYSTEM_RESULTS["python"]="SKIP"
            return
        fi
    fi

    local all_findings=()

    # Scan requirements.txt files
    find . -name "requirements*.txt" \
         -not -path "*/node_modules/*" \
         -not -path "*/.git/*" \
         -not -path "*/.venv/*" | while IFS= read -r req_file; do
        log "  Auditing ${req_file}..."
        pip-audit \
            --requirement "${req_file}" \
            --format json \
            --output "${REPORT_DIR}/pip-audit-$(echo "${req_file}" | tr '/' '_').json" \
            2>/dev/null || true
    done

    # Scan pyproject.toml if present
    if [[ -f "pyproject.toml" ]]; then
        log "  Auditing pyproject.toml..."
        pip-audit \
            --project . \
            --format json \
            --output "${REPORT_DIR}/pip-audit-pyproject.json" \
            2>/dev/null || true
    fi

    # Aggregate
    python3 - <<PYEOF
import json, glob

all_vulns = []
audit_files = glob.glob("${REPORT_DIR}/pip-audit-*.json")

for audit_file in audit_files:
    try:
        with open(audit_file) as f:
            data = json.load(f)
        for dep in data.get("dependencies", []):
            for vuln in dep.get("vulns", []):
                # pip-audit doesn't score severity directly; map by CVSS if available
                cvss = vuln.get("fix_versions", {})
                all_vulns.append({
                    "ecosystem": "python",
                    "package": dep.get("name", "unknown"),
                    "installed_version": dep.get("version", "unknown"),
                    "vuln_id": vuln.get("id", "N/A"),
                    "description": vuln.get("description", "")[:120],
                    "fix_versions": vuln.get("fix_versions", []),
                    "source": audit_file,
                })
    except (json.JSONDecodeError, KeyError):
        pass

print(f"python: {len(all_vulns)} vulnerable packages found")

# iGaming-specific: flag crypto/payment libraries
sensitive_pkgs = ["cryptography", "pycrypto", "requests", "jwt", "stripe",
                  "celery", "sqlalchemy", "django", "flask", "fastapi"]
for v in all_vulns:
    if any(p in v["package"].lower() for p in sensitive_pkgs):
        print(f"  ⚠ SENSITIVE PKG: {v['package']} {v['installed_version']} -- {v['vuln_id']}")

with open("${report_file}", "w") as f:
    json.dump({
        "ecosystem": "python",
        "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
        "count": len(all_vulns),
        "vulnerabilities": all_vulns
    }, f, indent=2)
PYEOF

    ECOSYSTEM_RESULTS["python"]="DONE"
    log_ok "pip-audit complete -- report: ${report_file}"
}

# ---------------------------------------------------------------------------
# govulncheck (Go)
# ---------------------------------------------------------------------------
audit_go() {
    log "Auditing Go dependencies..."
    local report_file="${REPORT_DIR}/go-vulncheck.json"

    if ! require_tool govulncheck; then
        if require_tool go; then
            log "Installing govulncheck..."
            go install golang.org/x/vuln/cmd/govulncheck@latest 2>/dev/null || true
        fi
        if ! require_tool govulncheck; then
            log_skip "govulncheck not available"
            ECOSYSTEM_RESULTS["go"]="SKIP"
            return
        fi
    fi

    if [[ ! -f "go.mod" ]]; then
        log_skip "No go.mod found"
        ECOSYSTEM_RESULTS["go"]="SKIP"
        return
    fi

    govulncheck -json ./... >"${report_file}" 2>/dev/null || true

    python3 - <<PYEOF
import json

try:
    with open("${report_file}") as f:
        # govulncheck outputs one JSON object per line
        findings = []
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "finding" in obj:
                    findings.append(obj["finding"])
            except json.JSONDecodeError:
                pass
    print(f"go: {len(findings)} vulnerable module(s) found")
    for f in findings[:10]:
        osv_id = f.get("osv", "unknown")
        trace = f.get("trace", [{}])
        mod = trace[0].get("module", "unknown") if trace else "unknown"
        print(f"  {osv_id}: {mod}")
except FileNotFoundError:
    print("go: no results file")
PYEOF

    ECOSYSTEM_RESULTS["go"]="DONE"
    log_ok "govulncheck complete -- report: ${report_file}"
}

# ---------------------------------------------------------------------------
# bundle-audit (Ruby)
# ---------------------------------------------------------------------------
audit_ruby() {
    log "Auditing Ruby dependencies..."
    local report_file="${REPORT_DIR}/bundle-audit.json"

    if [[ ! -f "Gemfile.lock" ]]; then
        log_skip "No Gemfile.lock found"
        ECOSYSTEM_RESULTS["ruby"]="SKIP"
        return
    fi

    if ! require_tool bundle-audit; then
        log_skip "bundle-audit not found (gem install bundler-audit)"
        ECOSYSTEM_RESULTS["ruby"]="SKIP"
        return
    fi

    bundle-audit check --update --format json \
        >"${report_file}" 2>/dev/null || true

    ECOSYSTEM_RESULTS["ruby"]="DONE"
    log_ok "bundle-audit complete -- report: ${report_file}"
}

# ---------------------------------------------------------------------------
# cargo audit (Rust)
# ---------------------------------------------------------------------------
audit_rust() {
    log "Auditing Rust dependencies..."
    local report_file="${REPORT_DIR}/cargo-audit.json"

    if [[ ! -f "Cargo.lock" ]]; then
        log_skip "No Cargo.lock found"
        ECOSYSTEM_RESULTS["rust"]="SKIP"
        return
    fi

    if ! require_tool cargo-audit; then
        if require_tool cargo; then
            log "Installing cargo-audit..."
            cargo install cargo-audit --quiet 2>/dev/null || true
        fi
        if ! require_tool cargo-audit; then
            log_skip "cargo-audit not available"
            ECOSYSTEM_RESULTS["rust"]="SKIP"
            return
        fi
    fi

    cargo audit --json >"${report_file}" 2>/dev/null || true

    python3 - <<PYEOF
import json
try:
    with open("${report_file}") as f:
        data = json.load(f)
    vulns = data.get("vulnerabilities", {}).get("list", [])
    print(f"rust: {len(vulns)} vulnerable crates")
    for v in vulns[:10]:
        adv = v.get("advisory", {})
        pkg = v.get("package", {}).get("name", "unknown")
        print(f"  {adv.get('id', 'N/A')}: {pkg} -- {adv.get('title', '')[:60]}")
except (FileNotFoundError, json.JSONDecodeError):
    print("rust: no results")
PYEOF

    ECOSYSTEM_RESULTS["rust"]="DONE"
    log_ok "cargo audit complete -- report: ${report_file}"
}

# ---------------------------------------------------------------------------
# OWASP Dependency-Check (Java/Maven/Gradle)
# ---------------------------------------------------------------------------
audit_java() {
    log "Auditing Java dependencies..."

    if [[ ! -f "pom.xml" ]] && [[ ! -f "build.gradle" ]] && [[ ! -f "build.gradle.kts" ]]; then
        log_skip "No Maven or Gradle build files found"
        ECOSYSTEM_RESULTS["java"]="SKIP"
        return
    fi

    if require_tool dependency-check; then
        dependency-check \
            --project "igaming-platform" \
            --scan . \
            --format JSON \
            --out "${REPORT_DIR}" \
            --failOnCVSS 7 \
            --suppression .dependency-check-suppressions.xml \
            >/dev/null 2>&1 || true

        python3 - <<PYEOF
import json, os
report_file = "${REPORT_DIR}/dependency-check-report.json"
if not os.path.exists(report_file):
    print("java: no dependency-check report found")
else:
    with open(report_file) as f:
        data = json.load(f)
    deps = data.get("dependencies", [])
    vuln_deps = [d for d in deps if d.get("vulnerabilities")]
    total = sum(len(d["vulnerabilities"]) for d in vuln_deps)
    high   = sum(1 for d in vuln_deps for v in d["vulnerabilities"]
                 if v.get("cvssv3", {}).get("baseScore", 0) >= 7.0)
    print(f"java: {total} total CVEs, {high} high/critical severity")
    for d in vuln_deps[:10]:
        for v in d["vulnerabilities"][:1]:
            score = v.get("cvssv3", {}).get("baseScore", v.get("cvssv2", {}).get("score", "N/A"))
            print(f"  CVSS {score}: {d.get('fileName','?')} -- {v.get('name','?')}")
PYEOF
        ECOSYSTEM_RESULTS["java"]="DONE"
        log_ok "dependency-check complete"
    else
        log_skip "OWASP dependency-check not found"
        ECOSYSTEM_RESULTS["java"]="SKIP"
    fi
}

# ---------------------------------------------------------------------------
# Trivy + Grype: container image audit
# ---------------------------------------------------------------------------
audit_containers() {
    if [[ -z "${IMAGE_TAG}" ]]; then
        log_skip "Container audit skipped (set IMAGE_TAG env var)"
        ECOSYSTEM_RESULTS["containers"]="SKIP"
        return
    fi

    log "Auditing container image: ${IMAGE_TAG}"

    if require_tool trivy; then
        trivy image \
            "${IMAGE_TAG}" \
            --severity "CRITICAL,HIGH,MEDIUM" \
            --ignore-unfixed \
            --format json \
            --output "${REPORT_DIR}/trivy-image.json" \
            2>/dev/null || true

        python3 - <<PYEOF
import json
try:
    with open("${REPORT_DIR}/trivy-image.json") as f:
        data = json.load(f)
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for result in data.get("Results", []):
        for vuln in result.get("Vulnerabilities", []) or []:
            sev = vuln.get("Severity", "UNKNOWN").upper()
            counts[sev] = counts.get(sev, 0) + 1
    print(f"container (trivy): critical={counts['CRITICAL']}, high={counts['HIGH']}, "
          f"medium={counts['MEDIUM']}, low={counts['LOW']}")
except (FileNotFoundError, json.JSONDecodeError):
    print("container: trivy scan results not available")
PYEOF
    fi

    if require_tool grype; then
        grype "${IMAGE_TAG}" \
            --output json \
            >"${REPORT_DIR}/grype-image.json" \
            2>/dev/null || true

        python3 - <<PYEOF
import json
try:
    with open("${REPORT_DIR}/grype-image.json") as f:
        data = json.load(f)
    counts = {}
    for match in data.get("matches", []):
        sev = match.get("vulnerability", {}).get("severity", "Unknown").upper()
        counts[sev] = counts.get(sev, 0) + 1
    summary = ", ".join(f"{k.lower()}={v}" for k, v in sorted(counts.items()))
    print(f"container (grype): {summary}")
except (FileNotFoundError, json.JSONDecodeError):
    print("container: grype scan results not available")
PYEOF
    fi

    ECOSYSTEM_RESULTS["containers"]="DONE"
    log_ok "Container audit complete"
}

# ---------------------------------------------------------------------------
# Snyk: multi-ecosystem commercial scanner (optional, requires token)
# ---------------------------------------------------------------------------
audit_snyk() {
    if [[ -z "${SNYK_TOKEN:-}" ]]; then
        log_skip "Snyk audit skipped (set SNYK_TOKEN env var)"
        ECOSYSTEM_RESULTS["snyk"]="SKIP"
        return
    fi

    log "Running Snyk multi-ecosystem audit..."

    if ! require_tool snyk; then
        log_skip "snyk CLI not found (npm install -g snyk)"
        ECOSYSTEM_RESULTS["snyk"]="SKIP"
        return
    fi

    snyk auth "${SNYK_TOKEN}" --quiet 2>/dev/null || true

    snyk test \
        --all-projects \
        --severity-threshold="${SEVERITY}" \
        --json \
        >"${REPORT_DIR}/snyk-test.json" 2>/dev/null || true

    python3 - <<PYEOF
import json
try:
    with open("${REPORT_DIR}/snyk-test.json") as f:
        data = json.load(f)
    # Snyk returns either a list (all-projects) or a single object
    if isinstance(data, list):
        all_issues = []
        for proj in data:
            all_issues.extend(proj.get("vulnerabilities", []))
    else:
        all_issues = data.get("vulnerabilities", [])

    severity_counts = {}
    for issue in all_issues:
        sev = issue.get("severity", "unknown").lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    summary = ", ".join(f"{k}={v}" for k, v in sorted(severity_counts.items()))
    print(f"snyk: {len(all_issues)} total vulnerabilities ({summary})")
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"snyk: could not parse results ({e})")
PYEOF

    ECOSYSTEM_RESULTS["snyk"]="DONE"
    log_ok "Snyk audit complete -- report: ${REPORT_DIR}/snyk-test.json"
}

# ---------------------------------------------------------------------------
# Consolidated report
# ---------------------------------------------------------------------------
generate_consolidated_report() {
    local report_file="${REPORT_DIR}/dependency-audit-summary.json"
    local text_report="${REPORT_DIR}/dependency-audit-summary.txt"

    python3 - <<PYEOF
import json, glob, os, datetime

report = {
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "severity_threshold": "${SEVERITY}",
    "repository": "$(git rev-parse --show-toplevel 2>/dev/null | xargs basename || echo 'unknown')",
    "commit": "$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')",
    "ecosystems": {},
    "iGaming_highlights": [],
}

# Load each ecosystem report
for fname in glob.glob("${REPORT_DIR}/*.json"):
    if "summary" in fname:
        continue
    try:
        with open(fname) as f:
            data = json.load(f)
        eco = data.get("ecosystem", os.path.basename(fname).replace(".json", ""))
        report["ecosystems"][eco] = {
            "file": fname,
            "vulnerability_count": (
                data.get("count", 0) or
                len(data.get("vulnerabilities", data.get("matches", []))) or
                sum(data.get("summary", {}).values() or [0])
            )
        }
    except (json.JSONDecodeError, TypeError):
        pass

# iGaming-specific highlight: payment ecosystem packages
# (populated from individual scans above)
report["iGaming_highlights"] = [
    "Verify payment processor SDK versions (Stripe, Adyen, Braintree) have no HIGH+ CVEs",
    "Confirm cryptography/JWT libraries are up to date (session hijacking risk)",
    "Check game engine dependencies for RNG-related vulnerabilities",
    "Review database driver versions for SQL injection vectors",
]

total_vulns = sum(
    eco_data.get("vulnerability_count", 0)
    for eco_data in report["ecosystems"].values()
)
report["total_vulnerabilities"] = total_vulns
report["status"] = "PASS" if total_vulns == 0 else "REVIEW_REQUIRED"

with open("${report_file}", "w") as f:
    json.dump(report, f, indent=2)

# Text summary
lines = [
    "=" * 60,
    "  Dependency Audit Summary",
    f"  {report['timestamp']}",
    f"  Repository: {report['repository']} @ {report['commit']}",
    "=" * 60,
    "",
    f"Severity threshold: {report['severity_threshold'].upper()}+",
    "",
    "Ecosystem Results:",
]
for eco, data in report["ecosystems"].items():
    count = data["vulnerability_count"]
    status = "✓ CLEAN" if count == 0 else f"⚠ {count} vulnerabilities"
    lines.append(f"  {eco:15s}  {status}")

lines += [
    "",
    f"Total vulnerabilities: {total_vulns}",
    f"Overall status: {report['status']}",
    "",
    "iGaming Security Highlights:",
]
for h in report["iGaming_highlights"]:
    lines.append(f"  - {h}")

with open("${text_report}", "w") as f:
    f.write("\n".join(lines) + "\n")

print("\n".join(lines))
PYEOF

    log "Consolidated report: ${report_file}"
    log "Text report:         ${text_report}"
}

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
log "Starting dependency audit (severity threshold: ${SEVERITY})"
log "Report directory: ${REPORT_DIR}"
log "Ecosystems: ${ECOSYSTEM}"
echo ""

should_run "npm"        && audit_npm
should_run "python"     && audit_python
should_run "go"         && audit_go
should_run "ruby"       && audit_ruby
should_run "rust"       && audit_rust
should_run "java"       && audit_java
should_run "containers" && audit_containers
should_run "snyk"       && audit_snyk

echo ""
generate_consolidated_report

# ---------------------------------------------------------------------------
# Exit decision
# ---------------------------------------------------------------------------
if [[ "${FAIL_ON_FINDINGS}" == "true" ]]; then
    # Check if any ecosystem reported non-zero findings
    FINDING_COUNT=$(python3 -c "
import json
with open('${REPORT_DIR}/dependency-audit-summary.json') as f:
    data = json.load(f)
print(data.get('total_vulnerabilities', 0))
" 2>/dev/null || echo "0")

    if [[ "${FINDING_COUNT}" -gt 0 ]]; then
        log_err "Audit FAILED: ${FINDING_COUNT} vulnerabilities found at ${SEVERITY}+ severity"
        exit 1
    fi
fi

log_ok "Dependency audit complete"
exit 0
