#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# trivy-ignore-audit.sh — Governance audit for Trivy CVE suppress rules.
#
# Detects unauthorized, ungoverned, or expired .trivyignore entries before
# they silently bypass the security gate in CI.
#
# Three detection layers:
#   1. Static  — parse .trivyignore / .trivyignore.yaml:
#                missing expiry, missing statement, expired entries, unapproved IDs
#   2. Git     — diff vs base branch: new/modified ignores trigger review gate
#   3. Dynamic — run trivy --show-suppressed: count CRITICAL/HIGH actually hidden
#
# Exit codes:
#   0 — all clear
#   1 — governance violation (missing expiry on CRITICAL, unapproved entry, etc.)
#   2 — new ignore added in this PR; security team review REQUIRED
#
# Usage (local):
#   bash security/trivy-ignore-audit.sh
#   SCAN_IMAGE=myapp:latest bash security/trivy-ignore-audit.sh
#
# Usage (CI — add to .gitlab-ci.yml or GitHub Actions):
#   BASE_BRANCH=main SCAN_IMAGE=$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA \
#     bash scripts/chapter-23b/security/trivy-ignore-audit.sh
#
# Env vars:
#   SCAN_IMAGE     — Docker image to scan in dynamic mode (optional)
#   SCAN_PATH      — Filesystem path to scan (default: .)
#   BASE_BRANCH    — Git base branch for diff gate (default: main)
#   IGNORE_FILE    — Path to .trivyignore plain-text file (default: auto-detect)
#   IGNORE_YAML    — Path to .trivyignore.yaml file (default: auto-detect)
#   APPROVED_FILE  — Allowlist of approved CVE ignores (default: approved-ignores.txt
#                    in same dir as this script)
#   FAIL_ON_REVIEW — Exit 2 when new ignores are added (default: 1, set 0 to warn only)

set -uo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────

SCAN_IMAGE="${SCAN_IMAGE:-}"
SCAN_PATH="${SCAN_PATH:-.}"
BASE_BRANCH="${BASE_BRANCH:-main}"
FAIL_ON_REVIEW="${FAIL_ON_REVIEW:-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPROVED_FILE="${APPROVED_FILE:-${SCRIPT_DIR}/approved-ignores.txt}"
TODAY="$(date +%Y-%m-%d)"

VIOLATIONS=0
REVIEW_REQUIRED=0

# Auto-detect ignore files from common locations
IGNORE_FILE="${IGNORE_FILE:-}"
IGNORE_YAML="${IGNORE_YAML:-}"
for candidate in .trivyignore trivyignore .trivy/ignore; do
    [[ -z "$IGNORE_FILE" && -f "$candidate" ]] && IGNORE_FILE="$candidate"
done
for candidate in .trivyignore.yaml .trivyignore.yml .trivy/ignore.yaml; do
    [[ -z "$IGNORE_YAML" && -f "$candidate" ]] && IGNORE_YAML="$candidate"
done

# ── Output helpers ────────────────────────────────────────────────────────────

red()    { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*" >&2; }
yellow() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*" >&2; }
green()  { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
info()   { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }
section(){ printf '\n\033[1;37m━━ %s ━━\033[0m\n' "$*"; }

violation() { red "$@"; VIOLATIONS=$(( VIOLATIONS + 1 )); }
review()    { yellow "$@"; REVIEW_REQUIRED=$(( REVIEW_REQUIRED + 1 )); }

# ── Helpers ───────────────────────────────────────────────────────────────────

is_approved() {
    local cve="$1"
    [[ ! -f "$APPROVED_FILE" ]] && return 1
    grep -qE "^[[:space:]]*${cve}[[:space:]]" "$APPROVED_FILE" 2>/dev/null
}

date_is_past() {
    local d="$1"
    [[ "$d" < "$TODAY" ]]
}


# ── Banner ────────────────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Trivy Ignore Governance Audit  —  ${TODAY}"
echo "  Base branch: ${BASE_BRANCH}  |  Approved list: $(basename "${APPROVED_FILE}")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ -z "$IGNORE_FILE" && -z "$IGNORE_YAML" ]]; then
    info "No .trivyignore or .trivyignore.yaml found — nothing to audit."
    echo ""
    green "No ignore files present — scanner output is unfiltered."
    exit 0
fi

# ── Layer 1: Static analysis of .trivyignore (plain text) ────────────────────

section "Layer 1 — Static: .trivyignore"

if [[ -n "$IGNORE_FILE" ]]; then
    info "Auditing: ${IGNORE_FILE}"
    total_entries=0
    no_expiry=0
    expired_entries=0
    unapproved=0

    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip blank lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue

        total_entries=$(( total_entries + 1 ))
        cve_id="$(echo "$line" | awk '{print $1}')"
        exp_field="$(echo "$line" | grep -oE 'exp:[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1 || true)"
        exp_date="$(echo "$exp_field" | cut -d: -f2)"

        # Check 1: missing expiration date
        if [[ -z "$exp_date" ]]; then
            violation "No expiry on '${cve_id}' — permanent bypass! Add: exp:YYYY-MM-DD"
            no_expiry=$(( no_expiry + 1 ))
        elif date_is_past "$exp_date"; then
            # Check 2: expired entry still active (should have been removed)
            violation "Expired ignore '${cve_id}' (exp:${exp_date}) is still in file — remove it!"
            expired_entries=$(( expired_entries + 1 ))
        else
            green "${cve_id}  (expires ${exp_date})"
        fi

        # Check 3: not in approved allowlist
        if ! is_approved "$cve_id"; then
            violation "Unapproved ignore '${cve_id}' — not in ${APPROVED_FILE}"
            unapproved=$(( unapproved + 1 ))
        fi
    done < "$IGNORE_FILE"

    info "Summary: ${total_entries} entries — ${no_expiry} without expiry, ${expired_entries} expired, ${unapproved} unapproved"
else
    info "No plain-text .trivyignore found."
fi

# ── Layer 1b: Static analysis of .trivyignore.yaml ───────────────────────────

section "Layer 1b — Static: .trivyignore.yaml"

if [[ -n "$IGNORE_YAML" ]]; then
    info "Auditing: ${IGNORE_YAML}"

    if ! command -v python3 &>/dev/null; then
        yellow "python3 not available — skipping YAML analysis"
    else
        python3 - <<PYEOF
import sys, yaml, datetime

today = datetime.date.today()
violations = 0
warnings = 0

try:
    with open("${IGNORE_YAML}") as f:
        data = yaml.safe_load(f) or {}
except Exception as e:
    print(f"\033[0;31m[FAIL]\033[0m Cannot parse ${IGNORE_YAML}: {e}", file=sys.stderr)
    sys.exit(1)

def check_section(section_name, entries):
    global violations, warnings
    if not entries:
        return
    for entry in entries:
        cve_id = entry.get("id", "<no-id>")
        statement = entry.get("statement", "")
        expired_at = entry.get("expired_at", None)
        paths = entry.get("paths", [])
        purls = entry.get("purls", [])

        ok = True

        # Missing statement — no audit trail
        if not statement:
            print(f"\033[1;33m[WARN]\033[0m [{section_name}] '{cve_id}' has no statement — add a reason for the ignore")
            warnings += 1
            ok = False

        # Missing expiration
        if not expired_at:
            print(f"\033[0;31m[FAIL]\033[0m [{section_name}] '{cve_id}' has no expired_at — permanent bypass!", file=sys.stderr)
            violations += 1
            ok = False
        else:
            exp = datetime.date.fromisoformat(str(expired_at))
            if exp < today:
                print(f"\033[0;31m[FAIL]\033[0m [{section_name}] '{cve_id}' expired on {expired_at} — remove stale entry!", file=sys.stderr)
                violations += 1
                ok = False

        # No path or purl constraint — overly broad ignore
        if not paths and not purls:
            print(f"\033[1;33m[WARN]\033[0m [{section_name}] '{cve_id}' has no paths/purls constraint — applies to ALL packages!", file=sys.stderr)
            warnings += 1

        if ok:
            exp_display = str(expired_at) if expired_at else "none"
            print(f"\033[0;32m[ OK ]\033[0m [{section_name}] {cve_id}  (expires {exp_display})")

check_section("vulnerabilities", data.get("vulnerabilities", []))
check_section("misconfigurations", data.get("misconfigurations", []))
check_section("secrets", data.get("secrets", []))
check_section("licenses", data.get("licenses", []))

print(f"\n\033[0;34m[INFO]\033[0m YAML audit: {violations} violation(s), {warnings} warning(s)")
sys.exit(violations)
PYEOF
        yaml_rc=$?
        VIOLATIONS=$(( VIOLATIONS + yaml_rc ))
    fi
else
    info "No .trivyignore.yaml found."
fi

# ── Layer 2: Git diff gate ────────────────────────────────────────────────────

section "Layer 2 — Git diff: New or modified ignore entries"

if git rev-parse --git-dir &>/dev/null; then
    changed_files="$(git diff --name-only "origin/${BASE_BRANCH}...HEAD" -- \
        '.trivyignore' '.trivyignore.yaml' '.trivyignore.yml' '**/.trivyignore' \
        2>/dev/null || true)"

    if [[ -n "$changed_files" ]]; then
        review "Ignore file(s) modified in this branch:"
        for f in $changed_files; do
            echo "       → ${f}"
        done

        echo ""
        info "Added lines (requires security team approval):"
        git diff "origin/${BASE_BRANCH}...HEAD" -- \
            '.trivyignore' '.trivyignore.yaml' '.trivyignore.yml' '**/.trivyignore' \
            2>/dev/null \
            | grep -E '^\+[^+]' \
            | grep -vE '^\+\+\+' \
            | sed 's/^+/  +/' \
            || true
    else
        green "No ignore files modified in this branch vs ${BASE_BRANCH}"
    fi
else
    info "Not a git repository — skipping git diff gate"
fi

# ── Layer 3: Dynamic — trivy --show-suppressed ────────────────────────────────

section "Layer 3 — Dynamic: trivy --show-suppressed"

if ! command -v trivy &>/dev/null; then
    info "trivy not found in PATH — skipping dynamic scan (install: brew install trivy)"
else
    trivy_args=(--format json --quiet --show-suppressed --exit-code 0)

    if [[ -n "$IGNORE_FILE" ]]; then
        trivy_args+=(--ignorefile "$IGNORE_FILE")
    fi
    if [[ -n "$IGNORE_YAML" ]]; then
        trivy_args+=(--ignorefile "$IGNORE_YAML")
    fi

    TMPFILE="$(mktemp /tmp/trivy-suppressed-XXXXXX.json)"
    # shellcheck disable=SC2064
    trap "rm -f ${TMPFILE}" EXIT

    if [[ -n "$SCAN_IMAGE" ]]; then
        info "Running: trivy image --show-suppressed ${SCAN_IMAGE}"
        trivy image "${trivy_args[@]}" "$SCAN_IMAGE" > "$TMPFILE" 2>/dev/null || true
    else
        info "Running: trivy fs --show-suppressed ${SCAN_PATH}"
        trivy fs "${trivy_args[@]}" --scanners vuln "$SCAN_PATH" > "$TMPFILE" 2>/dev/null || true
    fi

    if [[ -s "$TMPFILE" ]]; then
        python3 - <<PYEOF
import json, sys

try:
    with open("${TMPFILE}") as f:
        data = json.load(f)
except Exception as e:
    print(f"\033[1;33m[WARN]\033[0m Cannot parse trivy output: {e}", file=sys.stderr)
    sys.exit(0)

suppressed_critical = []
suppressed_high = []
suppressed_other = []

results = data if isinstance(data, list) else data.get("Results", [])
for result in results:
    # Trivy >= 0.50: suppressed findings in ExperimentalModifiedFindings
    for item in result.get("ExperimentalModifiedFindings", []):
        if item.get("Status") != "ignored" or item.get("Type") != "vulnerability":
            continue
        finding = item.get("Finding", {})
        sev = finding.get("Severity", "UNKNOWN")
        entry = {
            "id": finding.get("VulnerabilityID", "?"),
            "pkg": finding.get("PkgName", "?"),
            "severity": sev,
            "fixed_version": finding.get("FixedVersion", ""),
            "source": item.get("Source", ""),
        }
        if sev == "CRITICAL":
            suppressed_critical.append(entry)
        elif sev == "HIGH":
            suppressed_high.append(entry)
        else:
            suppressed_other.append(entry)
    # Trivy < 0.50 fallback: SuppressedBy field inside Vulnerabilities
    for vuln in result.get("Vulnerabilities", []):
        if not vuln.get("SuppressedBy"):
            continue
        sev = vuln.get("Severity", "UNKNOWN")
        entry = {
            "id": vuln.get("VulnerabilityID", "?"),
            "pkg": vuln.get("PkgName", "?"),
            "severity": sev,
            "fixed_version": vuln.get("FixedVersion", ""),
            "source": vuln.get("SuppressedBy", ""),
        }
        if sev == "CRITICAL":
            suppressed_critical.append(entry)
        elif sev == "HIGH":
            suppressed_high.append(entry)
        else:
            suppressed_other.append(entry)

total = len(suppressed_critical) + len(suppressed_high) + len(suppressed_other)

if total == 0:
    print("\033[0;32m[ OK ]\033[0m No suppressed findings detected")
    sys.exit(0)

print(f"\033[0;34m[INFO]\033[0m {total} finding(s) suppressed by ignore rules:")
print()

violations = 0
for v in suppressed_critical:
    fix = f"  (fix: {v['fixed_version']})" if v['fixed_version'] else "  (NO FIX AVAILABLE)"
    has_fix = bool(v['fixed_version'])
    marker = "\033[0;31m[FAIL]\033[0m" if has_fix else "\033[1;33m[WARN]\033[0m"
    print(f"  {marker} CRITICAL  {v['id']}  [{v['pkg']}]{fix}")
    if has_fix:
        violations += 1

for v in suppressed_high:
    fix = f"  (fix: {v['fixed_version']})" if v['fixed_version'] else "  (NO FIX AVAILABLE)"
    has_fix = bool(v['fixed_version'])
    marker = "\033[1;33m[WARN]\033[0m" if has_fix else "\033[0;34m[INFO]\033[0m"
    print(f"  {marker} HIGH      {v['id']}  [{v['pkg']}]{fix}")


for v in suppressed_other:
    print(f"  \033[0;34m[INFO]\033[0m {v['severity']:<10}  {v['id']}  [{v['pkg']}]")

if violations:
    print()
    print(f"\033[0;31m[FAIL]\033[0m {violations} CRITICAL finding(s) suppressed despite having a fix version!", file=sys.stderr)
    print("       This is the highest-risk bypass: a known fix exists but is hidden from CI.", file=sys.stderr)

sys.exit(violations)
PYEOF
        dyn_rc=$?
        VIOLATIONS=$(( VIOLATIONS + dyn_rc ))
    else
        info "Trivy returned empty output — no vulnerabilities found (or scan failed silently)"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$REVIEW_REQUIRED" -gt 0 ]]; then
    yellow "${REVIEW_REQUIRED} ignore file(s) modified — security team review REQUIRED before merge."
    echo "  Add SEC team as required reviewer on this PR."
fi

if [[ "$VIOLATIONS" -eq 0 && "$REVIEW_REQUIRED" -eq 0 ]]; then
    green "All ignore rules are governed — no unauthorized bypasses detected."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
elif [[ "$VIOLATIONS" -gt 0 ]]; then
    red "${VIOLATIONS} governance violation(s) — fix before merging."
    echo "  Remediation:"
    echo "    1. Add exp:YYYY-MM-DD to every .trivyignore entry"
    echo "    2. Add expired_at + statement to every .trivyignore.yaml entry"
    echo "    3. Get each CVE approved in ${APPROVED_FILE}"
    echo "    4. Remove entries for CVEs that now have a fix version"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$FAIL_ON_REVIEW" == "1" && "$REVIEW_REQUIRED" -gt 0 ]]; then
    exit 2
fi
exit 0
