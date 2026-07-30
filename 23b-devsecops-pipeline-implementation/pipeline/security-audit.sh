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

set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 1

FAILED=0
MISSING=0
SKIPS=()
AUDIT_MAX_JOBS="${AUDIT_MAX_JOBS:-24}"
PIP_AUDIT_MAX_JOBS="${PIP_AUDIT_MAX_JOBS:-8}"
PIP_AUDIT_DESC="${PIP_AUDIT_DESC:-off}"
PIP_AUDIT_NO_DEPS="${PIP_AUDIT_NO_DEPS:-1}"
PIP_AUDIT_TIMEOUT="${PIP_AUDIT_TIMEOUT:-10}"
PIP_AUDIT_PROCESS_TIMEOUT="${PIP_AUDIT_PROCESS_TIMEOUT:-60}"
GITLEAKS_HISTORY="${GITLEAKS_HISTORY:-0}"
GITLEAKS_MAX_TARGET_MB="${GITLEAKS_MAX_TARGET_MB:-5}"
GITLEAKS_TIMEOUT="${GITLEAKS_TIMEOUT:-120}"
BANDIT_SEVERITY="${BANDIT_SEVERITY:-high}"
BANDIT_CONFIDENCE="${BANDIT_CONFIDENCE:-high}"
TRIVY_SCANNERS="${TRIVY_SCANNERS:-vuln,misconfig}"
TRIVY_PARALLEL="${TRIVY_PARALLEL:-0}"
TRIVY_SKIP_DIRS="${TRIVY_SKIP_DIRS:-.git,node_modules,.playwright-mcp,dist,build,.next,.nuxt,coverage,.venv,venv,.tox,.mypy_cache,.pytest_cache,.ruff_cache,tmp,vendor}"
TRIVY_SKIP_FILES="${TRIVY_SKIP_FILES:-**/*.log}"

detect_cpu_count() {
  getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4
}

detect_jobs() {
  local manifest_count="${1:-1}"
  local cpu_count
  local jobs

  cpu_count="$(detect_cpu_count)"
  jobs=$((cpu_count * 2))

  if [ "$jobs" -lt 4 ]; then
    jobs=4
  fi

  if [ "$jobs" -gt "$AUDIT_MAX_JOBS" ]; then
    jobs="$AUDIT_MAX_JOBS"
  fi

  if [ "$manifest_count" -gt 0 ] && [ "$jobs" -gt "$manifest_count" ]; then
    jobs="$manifest_count"
  fi

  if [ "$jobs" -lt 1 ]; then
    jobs=1
  fi

  echo "$jobs"
}

AUDIT_JOBS="${AUDIT_JOBS:-auto}"

usage() {
  cat <<'USAGE'
Usage: scripts/security-audit.sh [--jobs N|auto|max] [--skip CHECK]

Runs the local security audit gate:
  - pip-audit for requirements*.txt
  - pip-audit for pyproject.toml projects with [project]
  - npm audit for package-lock.json
  - pnpm audit for pnpm-lock.yaml
  - govulncheck for go.mod
  - cargo audit for Cargo.lock
  - gitleaks, semgrep, bandit, checkov, and trivy when configured

Required tools are not installed automatically.

Options:
  --jobs VALUE    Parallel workers for manifest-heavy checks.
                  N: exact worker count.
                  auto: CPU count x 2, capped by AUDIT_MAX_JOBS and file count.
                  max: file count capped by AUDIT_MAX_JOBS.
                  Defaults to AUDIT_JOBS or auto. AUDIT_MAX_JOBS defaults to 24.
  --skip CHECK    Skip a check. Valid names: pip-audit, npm-audit,
                  pnpm-audit, govulncheck, cargo-audit, gitleaks,
                  semgrep, bandit, checkov, trivy.

Speed controls:
  AUDIT_MAX_JOBS  Caps manifest audit workers. Defaults to 24.
  PIP_AUDIT_MAX_JOBS Caps pip-audit workers to avoid index/resolver contention. Defaults to 8.
  PIP_AUDIT_NO_DEPS Set to 0 for full resolver mode. Defaults to 1.
  PIP_AUDIT_DESC Include vulnerability descriptions. Defaults to off.
  PIP_AUDIT_TIMEOUT Socket timeout in seconds. Defaults to 10.
  PIP_AUDIT_PROCESS_TIMEOUT Wall-clock timeout per pip-audit process. Defaults to 60.
  GITLEAKS_HISTORY Set to 1 to scan full git history. Defaults to current tree.
  GITLEAKS_MAX_TARGET_MB Skips larger files in the fast gate. Defaults to 5.
  GITLEAKS_TIMEOUT Timeout in seconds for the fast secret scan. Defaults to 120.
  BANDIT_SEVERITY Minimum Bandit severity: low, medium, or high. Defaults to high.
  BANDIT_CONFIDENCE Minimum Bandit confidence: low, medium, or high. Defaults to high.
  TRIVY_SCANNERS  Defaults to vuln,misconfig. Gitleaks handles secrets.
  TRIVY_PARALLEL  Trivy goroutines. Defaults to 0 for Trivy auto-detect.
  TRIVY_SKIP_DIRS Comma-separated generated/local directories skipped by Trivy.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --jobs)
      if [ -z "${2:-}" ]; then
        echo "ERROR: --jobs requires a numeric value"
        exit 2
      fi
      AUDIT_JOBS="$2"
      shift 2
      ;;
    --skip)
      if [ -z "${2:-}" ]; then
        echo "ERROR: --skip requires a check name"
        exit 2
      fi
      SKIPS+=("$2")
      shift 2
      ;;
    *)
      echo "ERROR: unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

case "$AUDIT_MAX_JOBS" in
  ''|*[!0-9]*)
    echo "ERROR: AUDIT_MAX_JOBS must be a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: AUDIT_MAX_JOBS must be greater than zero"
    exit 2
    ;;
esac

case "$PIP_AUDIT_MAX_JOBS" in
  ''|*[!0-9]*)
    echo "ERROR: PIP_AUDIT_MAX_JOBS must be a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: PIP_AUDIT_MAX_JOBS must be greater than zero"
    exit 2
    ;;
esac

case "$PIP_AUDIT_DESC" in
  on|off|auto)
    ;;
  *)
    echo "ERROR: PIP_AUDIT_DESC must be on, off, or auto"
    exit 2
    ;;
esac

case "$PIP_AUDIT_NO_DEPS" in
  0|1)
    ;;
  *)
    echo "ERROR: PIP_AUDIT_NO_DEPS must be 0 or 1"
    exit 2
    ;;
esac

case "$PIP_AUDIT_TIMEOUT" in
  ''|*[!0-9]*)
    echo "ERROR: PIP_AUDIT_TIMEOUT must be a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: PIP_AUDIT_TIMEOUT must be greater than zero"
    exit 2
    ;;
esac

case "$PIP_AUDIT_PROCESS_TIMEOUT" in
  ''|*[!0-9]*)
    echo "ERROR: PIP_AUDIT_PROCESS_TIMEOUT must be a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: PIP_AUDIT_PROCESS_TIMEOUT must be greater than zero"
    exit 2
    ;;
esac

case "$GITLEAKS_HISTORY" in
  0|1)
    ;;
  *)
    echo "ERROR: GITLEAKS_HISTORY must be 0 or 1"
    exit 2
    ;;
esac

case "$GITLEAKS_MAX_TARGET_MB" in
  ''|*[!0-9]*)
    echo "ERROR: GITLEAKS_MAX_TARGET_MB must be a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: GITLEAKS_MAX_TARGET_MB must be greater than zero"
    exit 2
    ;;
esac

case "$GITLEAKS_TIMEOUT" in
  ''|*[!0-9]*)
    echo "ERROR: GITLEAKS_TIMEOUT must be a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: GITLEAKS_TIMEOUT must be greater than zero"
    exit 2
    ;;
esac

case "$TRIVY_PARALLEL" in
  ''|*[!0-9]*)
    echo "ERROR: TRIVY_PARALLEL must be a non-negative integer"
    exit 2
    ;;
esac

case "$AUDIT_JOBS" in
  auto|max)
    ;;
  ''|*[!0-9]*)
    echo "ERROR: --jobs/AUDIT_JOBS must be auto, max, or a positive integer"
    exit 2
    ;;
  0)
    echo "ERROR: --jobs/AUDIT_JOBS must be greater than zero"
    exit 2
    ;;
esac

section() {
  printf '\n== %s ==\n' "$1"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

is_skipped() {
  local check

  if [ "${#SKIPS[@]}" -eq 0 ]; then
    return 1
  fi

  for check in "${SKIPS[@]}"; do
    if [ "$check" = "$1" ]; then
      return 0
    fi
  done

  return 1
}

skip_section() {
  section "$1"
  printf 'Skipped via --skip %s.\n' "$2"
}

mark_missing() {
  MISSING=1
  printf 'MISSING: %s\n' "$1"
  printf 'Install: %s\n' "$2"
}

mark_failed() {
  FAILED=1
  printf 'FAILED: %s\n' "$1"
}

matching_files() {
  local name="$1"
  find . \
    \( -path "./.git" -o -path "*/.git" -o -path "*/node_modules" -o -path "*/.venv" -o -path "*/venv" \) -prune -o \
    -type f -name "$name" -print0
}

has_matching_files() {
  local name="$1"
  while IFS= read -r -d '' _; do
    return 0
  done < <(matching_files "$name")
  return 1
}

count_matching_files() {
  local name="$1"
  local count=0

  while IFS= read -r -d '' _; do
    count=$((count + 1))
  done < <(matching_files "$name")

  echo "$count"
}

resolve_jobs() {
  local manifest_count="$1"

  case "$AUDIT_JOBS" in
    auto)
      detect_jobs "$manifest_count"
      ;;
    max)
      if [ "$manifest_count" -lt "$AUDIT_MAX_JOBS" ]; then
        echo "$manifest_count"
      else
        echo "$AUDIT_MAX_JOBS"
      fi
      ;;
    *)
      echo "$AUDIT_JOBS"
      ;;
  esac
}

resolve_pip_audit_jobs() {
  local manifest_count="$1"
  local jobs

  jobs="$(resolve_jobs "$manifest_count")"
  if [ "$jobs" -gt "$PIP_AUDIT_MAX_JOBS" ]; then
    jobs="$PIP_AUDIT_MAX_JOBS"
  fi

  echo "$jobs"
}

bandit_severity_flag() {
  case "$BANDIT_SEVERITY" in
    low) echo "-l" ;;
    medium) echo "-ll" ;;
    high) echo "-lll" ;;
    *)
      echo "ERROR: BANDIT_SEVERITY must be low, medium, or high" >&2
      exit 2
      ;;
  esac
}

bandit_confidence_flag() {
  case "$BANDIT_CONFIDENCE" in
    low) echo "-i" ;;
    medium) echo "-ii" ;;
    high) echo "-iii" ;;
    *)
      echo "ERROR: BANDIT_CONFIDENCE must be low, medium, or high" >&2
      exit 2
      ;;
  esac
}

run_simple_check() {
  local label="$1"
  shift

  section "$label"
  if ! "$@"; then
    mark_failed "$label"
  fi
}

audit_pyprojects_parallel() {
  local tmpdir
  local rel
  local id
  local status_file
  local rc=0

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/security-audit-pyproject.XXXXXX")"
  export PIP_AUDIT_DESC PIP_AUDIT_PROCESS_TIMEOUT PIP_AUDIT_TIMEOUT

  # shellcheck disable=SC2016
  matching_files "pyproject.toml" |
    xargs -0 -n1 -P "$AUDIT_JOBS" bash -c '
      run_pip_audit() {
        if command -v gtimeout >/dev/null 2>&1; then
          gtimeout "$PIP_AUDIT_PROCESS_TIMEOUT" pip-audit "$@"
        elif command -v timeout >/dev/null 2>&1; then
          timeout "$PIP_AUDIT_PROCESS_TIMEOUT" pip-audit "$@"
        else
          perl -e "alarm shift; exec @ARGV" "$PIP_AUDIT_PROCESS_TIMEOUT" pip-audit "$@"
        fi
      }

      tmpdir="$1"
      pyproject="$2"
      rel="${pyproject#./}"
      dir="$(dirname "$pyproject")"
      dirrel="${dir#./}"
      id="$(printf "%s" "$rel" | cksum | awk "{print \$1}")"
      log="$tmpdir/$id.log"
      status_file="$tmpdir/$id.status"

      {
        if grep -q "^\[project\]" "$pyproject"; then
          printf -- "--- pip-audit project %s ---\n" "$dirrel"
          pip_args=(--desc "$PIP_AUDIT_DESC" --progress-spinner off --timeout "$PIP_AUDIT_TIMEOUT")
          if run_pip_audit "$dir" "${pip_args[@]}"; then
            echo 0 > "$status_file"
          else
            audit_rc=$?
            if [ "$audit_rc" -eq 124 ]; then
              printf "ERROR: pip-audit timed out after %ss.\n" "$PIP_AUDIT_PROCESS_TIMEOUT"
            fi
            echo "$audit_rc" > "$status_file"
          fi
        elif grep -q "^\[tool\.poetry\]" "$pyproject"; then
          if [ -f "$dir/poetry.lock" ]; then
            lock_req="$tmpdir/$id.requirements.txt"
            printf -- "--- pip-audit locked Poetry project %s ---\n" "$dirrel"
            awk '"'"'
              function emit() {
                if (name != "" && version != "") {
                  if (marker != "") print name "==" version "; " marker
                  else print name "==" version
                }
              }
              /^\[\[package\]\]/ {
                emit()
                name=""
                version=""
                marker=""
                package_top=1
                next
              }
              /^\[/ {
                package_top=0
                next
              }
              package_top && /^name = / {
                name=$0
                sub(/^name = "/, "", name)
                sub(/"$/, "", name)
                next
              }
              package_top && /^version = / {
                version=$0
                sub(/^version = "/, "", version)
                sub(/"$/, "", version)
                next
              }
              package_top && /^markers = "/ {
                marker=$0
                sub(/^markers = "/, "", marker)
                sub(/"$/, "", marker)
                gsub(/\\"/, "\"", marker)
                next
              }
              END {
                emit()
              }
            '"'"' "$dir/poetry.lock" | sort -u > "$lock_req"
            pip_args=(--no-deps --desc "$PIP_AUDIT_DESC" --progress-spinner off --timeout "$PIP_AUDIT_TIMEOUT")
            if run_pip_audit -r "$lock_req" "${pip_args[@]}"; then
              echo 0 > "$status_file"
            else
              audit_rc=$?
              if [ "$audit_rc" -eq 124 ]; then
                printf "ERROR: pip-audit timed out after %ss.\n" "$PIP_AUDIT_PROCESS_TIMEOUT"
              fi
              echo "$audit_rc" > "$status_file"
            fi
          elif [ -f "$dir/requirements-audit.txt" ]; then
            printf "INFO: %s is a Poetry project covered by %s.\n" "$rel" "${dirrel}/requirements-audit.txt"
            echo 0 > "$status_file"
          else
            printf "WARN: %s is a Poetry project; export requirements-audit.txt or commit a supported lock file for pip-audit coverage.\n" "$rel"
            echo 0 > "$status_file"
          fi
        else
          printf "INFO: %s has no [project] or [tool.poetry] dependency table.\n" "$rel"
          echo 0 > "$status_file"
        fi
      } > "$log" 2>&1
    ' _ "$tmpdir"

  while IFS= read -r -d '' pyproject; do
    rel="${pyproject#./}"
    id="$(printf "%s" "$rel" | cksum | awk '{print $1}')"
    status_file="$tmpdir/$id.status"

    if [ -f "$tmpdir/$id.log" ]; then
      cat "$tmpdir/$id.log"
    else
      printf 'ERROR: pyproject audit worker produced no log for %s\n' "$rel"
    fi

    if [ ! -f "$status_file" ] || [ "$(cat "$status_file")" -ne 0 ]; then
      mark_failed "pip-audit project ${rel}"
      rc=1
    fi
  done < <(matching_files "pyproject.toml")

  rm -rf "$tmpdir"
  return "$rc"
}

audit_requirements_parallel() {
  local tmpdir
  local rel
  local id
  local status_file
  local rc=0

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/security-audit.XXXXXX")"
  export PIP_AUDIT_DESC PIP_AUDIT_NO_DEPS PIP_AUDIT_PROCESS_TIMEOUT PIP_AUDIT_TIMEOUT

  # shellcheck disable=SC2016
  matching_files "requirements*.txt" |
    xargs -0 -n1 -P "$AUDIT_JOBS" bash -c '
      run_pip_audit() {
        if command -v gtimeout >/dev/null 2>&1; then
          gtimeout "$PIP_AUDIT_PROCESS_TIMEOUT" pip-audit "$@"
        elif command -v timeout >/dev/null 2>&1; then
          timeout "$PIP_AUDIT_PROCESS_TIMEOUT" pip-audit "$@"
        else
          perl -e "alarm shift; exec @ARGV" "$PIP_AUDIT_PROCESS_TIMEOUT" pip-audit "$@"
        fi
      }

      tmpdir="$1"
      req="$2"
      rel="${req#./}"
      id="$(printf "%s" "$rel" | cksum | awk "{print \$1}")"
      log="$tmpdir/$id.log"
      status_file="$tmpdir/$id.status"

      {
        printf -- "--- pip-audit %s ---\n" "$rel"
        pip_args=(--desc "$PIP_AUDIT_DESC" --progress-spinner off --timeout "$PIP_AUDIT_TIMEOUT")
        if [ "$PIP_AUDIT_NO_DEPS" = "1" ]; then
          pip_args+=(--no-deps)
        fi
        if run_pip_audit -r "$req" "${pip_args[@]}"; then
          echo 0 > "$status_file"
        else
          audit_rc=$?
          if [ "$audit_rc" -eq 124 ]; then
            printf "ERROR: pip-audit timed out after %ss.\n" "$PIP_AUDIT_PROCESS_TIMEOUT"
          fi
          echo "$audit_rc" > "$status_file"
        fi
      } > "$log" 2>&1
    ' _ "$tmpdir"

  while IFS= read -r -d '' req; do
    rel="${req#./}"
    id="$(printf "%s" "$rel" | cksum | awk '{print $1}')"
    status_file="$tmpdir/$id.status"

    if [ -f "$tmpdir/$id.log" ]; then
      cat "$tmpdir/$id.log"
    else
      printf -- '--- pip-audit %s ---\n' "$rel"
      printf 'ERROR: audit worker produced no log\n'
    fi

    if [ ! -f "$status_file" ] || [ "$(cat "$status_file")" -ne 0 ]; then
      mark_failed "pip-audit $rel"
      rc=1
    fi
  done < <(matching_files "requirements*.txt")

  rm -rf "$tmpdir"
  return "$rc"
}

audit_package_locks_parallel() {
  local tmpdir
  local rel
  local id
  local status_file
  local rc=0

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/security-audit-npm.XXXXXX")"

  # shellcheck disable=SC2016
  matching_files "package-lock.json" |
    xargs -0 -n1 -P "$AUDIT_JOBS" bash -c '
      tmpdir="$1"
      lock="$2"
      dir="$(dirname "$lock")"
      rel="${dir#./}"
      id="$(printf "%s" "${lock#./}" | cksum | awk "{print \$1}")"
      log="$tmpdir/$id.log"
      status_file="$tmpdir/$id.status"

      {
        printf -- "--- npm audit %s ---\n" "$rel"
        if (cd "$dir" && npm audit --audit-level=high); then
          echo 0 > "$status_file"
        else
          audit_rc=$?
          echo "$audit_rc" > "$status_file"
        fi
      } > "$log" 2>&1
    ' _ "$tmpdir"

  while IFS= read -r -d '' lock; do
    rel="${lock#./}"
    id="$(printf "%s" "$rel" | cksum | awk '{print $1}')"
    status_file="$tmpdir/$id.status"

    cat "$tmpdir/$id.log"
    if [ ! -f "$status_file" ] || [ "$(cat "$status_file")" -ne 0 ]; then
      mark_failed "npm audit ${rel}"
      rc=1
    fi
  done < <(matching_files "package-lock.json")

  rm -rf "$tmpdir"
  return "$rc"
}

audit_pnpm_locks_parallel() {
  local pnpm_cmd="$1"
  local tmpdir
  local rel
  local id
  local status_file
  local rc=0

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/security-audit-pnpm.XXXXXX")"

  # shellcheck disable=SC2016
  matching_files "pnpm-lock.yaml" |
    xargs -0 -n1 -P "$AUDIT_JOBS" bash -c '
      tmpdir="$1"
      pnpm_cmd="$2"
      lock="$3"
      dir="$(dirname "$lock")"
      rel="${dir#./}"
      id="$(printf "%s" "${lock#./}" | cksum | awk "{print \$1}")"
      log="$tmpdir/$id.log"
      status_file="$tmpdir/$id.status"

      {
        printf -- "--- %s audit %s ---\n" "$pnpm_cmd" "$rel"
        if (cd "$dir" && $pnpm_cmd audit --audit-level high); then
          echo 0 > "$status_file"
        else
          audit_rc=$?
          echo "$audit_rc" > "$status_file"
        fi
      } > "$log" 2>&1
    ' _ "$tmpdir" "$pnpm_cmd"

  while IFS= read -r -d '' lock; do
    rel="${lock#./}"
    id="$(printf "%s" "$rel" | cksum | awk '{print $1}')"
    status_file="$tmpdir/$id.status"

    cat "$tmpdir/$id.log"
    if [ ! -f "$status_file" ] || [ "$(cat "$status_file")" -ne 0 ]; then
      mark_failed "pnpm audit ${rel}"
      rc=1
    fi
  done < <(matching_files "pnpm-lock.yaml")

  rm -rf "$tmpdir"
  return "$rc"
}

audit_go_mods_parallel() {
  local tmpdir
  local rel
  local id
  local status_file
  local rc=0

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/security-audit-go.XXXXXX")"

  # shellcheck disable=SC2016
  matching_files "go.mod" |
    xargs -0 -n1 -P "$AUDIT_JOBS" bash -c '
      tmpdir="$1"
      mod="$2"
      dir="$(dirname "$mod")"
      rel="${dir#./}"
      id="$(printf "%s" "${mod#./}" | cksum | awk "{print \$1}")"
      log="$tmpdir/$id.log"
      status_file="$tmpdir/$id.status"

      {
        printf -- "--- govulncheck %s ---\n" "$rel"
        if (cd "$dir" && govulncheck ./...); then
          echo 0 > "$status_file"
        else
          audit_rc=$?
          echo "$audit_rc" > "$status_file"
        fi
      } > "$log" 2>&1
    ' _ "$tmpdir"

  while IFS= read -r -d '' mod; do
    rel="${mod#./}"
    id="$(printf "%s" "$rel" | cksum | awk '{print $1}')"
    status_file="$tmpdir/$id.status"

    cat "$tmpdir/$id.log"
    if [ ! -f "$status_file" ] || [ "$(cat "$status_file")" -ne 0 ]; then
      mark_failed "govulncheck ${rel}"
      rc=1
    fi
  done < <(matching_files "go.mod")

  rm -rf "$tmpdir"
  return "$rc"
}

audit_cargo_locks_parallel() {
  local tmpdir
  local rel
  local id
  local status_file
  local rc=0

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/security-audit-cargo.XXXXXX")"

  # shellcheck disable=SC2016
  matching_files "Cargo.lock" |
    xargs -0 -n1 -P "$AUDIT_JOBS" bash -c '
      tmpdir="$1"
      lock="$2"
      dir="$(dirname "$lock")"
      rel="${dir#./}"
      id="$(printf "%s" "${lock#./}" | cksum | awk "{print \$1}")"
      log="$tmpdir/$id.log"
      status_file="$tmpdir/$id.status"

      {
        printf -- "--- cargo audit %s ---\n" "$rel"
        if (cd "$dir" && cargo audit); then
          echo 0 > "$status_file"
        else
          audit_rc=$?
          echo "$audit_rc" > "$status_file"
        fi
      } > "$log" 2>&1
    ' _ "$tmpdir"

  while IFS= read -r -d '' lock; do
    rel="${lock#./}"
    id="$(printf "%s" "$rel" | cksum | awk '{print $1}')"
    status_file="$tmpdir/$id.status"

    cat "$tmpdir/$id.log"
    if [ ! -f "$status_file" ] || [ "$(cat "$status_file")" -ne 0 ]; then
      mark_failed "cargo audit ${rel}"
      rc=1
    fi
  done < <(matching_files "Cargo.lock")

  rm -rf "$tmpdir"
  return "$rc"
}

MANIFEST_COUNT=$(( \
  $(count_matching_files "requirements*.txt") + \
  $(count_matching_files "pyproject.toml") + \
  $(count_matching_files "package-lock.json") + \
  $(count_matching_files "pnpm-lock.yaml") + \
  $(count_matching_files "go.mod") + \
  $(count_matching_files "Cargo.lock") \
))
AUDIT_JOBS="$(resolve_jobs "$MANIFEST_COUNT")"
PIP_AUDIT_JOBS="$(resolve_pip_audit_jobs "$MANIFEST_COUNT")"

section "Audit worker plan"
printf 'Manifest-heavy checks will use %s parallel workers (cpu=%s, manifests=%s, max=%s).\n' \
  "$AUDIT_JOBS" "$(detect_cpu_count)" "$MANIFEST_COUNT" "$AUDIT_MAX_JOBS"
printf 'Python dependency checks will use %s parallel workers (pip-audit max=%s, process timeout=%ss).\n' \
  "$PIP_AUDIT_JOBS" "$PIP_AUDIT_MAX_JOBS" "$PIP_AUDIT_PROCESS_TIMEOUT"

if is_skipped "pip-audit"; then
  skip_section "Python dependency audit" "pip-audit"
elif has_matching_files "requirements*.txt"; then
  section "Python dependency audit"
  if command_exists pip-audit; then
    printf 'Running requirements audits with %s parallel workers across %s manifest files.\n' "$PIP_AUDIT_JOBS" "$(count_matching_files "requirements*.txt")"
    AUDIT_JOBS="$PIP_AUDIT_JOBS" audit_requirements_parallel || true
  else
    mark_missing "pip-audit" "python -m pip install pip-audit"
  fi
else
  section "Python dependency audit"
  echo "No requirements files found."
fi

if is_skipped "pip-audit"; then
  skip_section "Python pyproject audit" "pip-audit"
elif has_matching_files "pyproject.toml"; then
  section "Python pyproject audit"
  if command_exists pip-audit; then
    printf 'Running pyproject audits with %s parallel workers across %s manifest files.\n' "$PIP_AUDIT_JOBS" "$(count_matching_files "pyproject.toml")"
    AUDIT_JOBS="$PIP_AUDIT_JOBS" audit_pyprojects_parallel || true
  else
    mark_missing "pip-audit" "python -m pip install pip-audit"
  fi
else
  section "Python pyproject audit"
  echo "No pyproject.toml files found."
fi

if is_skipped "npm-audit"; then
  skip_section "NPM dependency audit" "npm-audit"
elif has_matching_files "package-lock.json"; then
  section "NPM dependency audit"
  if command_exists npm; then
    printf 'Running npm audits with %s parallel workers across %s lock files.\n' "$AUDIT_JOBS" "$(count_matching_files "package-lock.json")"
    audit_package_locks_parallel || true
  else
    mark_missing "npm" "Install Node.js with npm."
  fi
else
  section "NPM dependency audit"
  echo "No package-lock.json files found."
fi

if is_skipped "pnpm-audit"; then
  skip_section "PNPM dependency audit" "pnpm-audit"
elif has_matching_files "pnpm-lock.yaml"; then
  section "PNPM dependency audit"
  if command_exists pnpm; then
    printf 'Running pnpm audits with %s parallel workers across %s lock files.\n' "$AUDIT_JOBS" "$(count_matching_files "pnpm-lock.yaml")"
    audit_pnpm_locks_parallel "pnpm" || true
  elif command_exists corepack; then
    printf 'Running corepack pnpm audits with %s parallel workers across %s lock files.\n' "$AUDIT_JOBS" "$(count_matching_files "pnpm-lock.yaml")"
    audit_pnpm_locks_parallel "corepack pnpm" || true
  else
    mark_missing "pnpm" "corepack enable && corepack prepare pnpm@10.30.1 --activate"
  fi
else
  section "PNPM dependency audit"
  echo "No pnpm-lock.yaml files found."
fi

if is_skipped "govulncheck"; then
  skip_section "Go vulnerability audit" "govulncheck"
elif has_matching_files "go.mod"; then
  section "Go vulnerability audit"
  if command_exists govulncheck; then
    printf 'Running Go vulnerability audits with %s parallel workers across %s modules.\n' "$AUDIT_JOBS" "$(count_matching_files "go.mod")"
    audit_go_mods_parallel || true
  else
    mark_missing "govulncheck" "go install golang.org/x/vuln/cmd/govulncheck@latest"
  fi
else
  section "Go vulnerability audit"
  echo "No go.mod files found."
fi

if is_skipped "cargo-audit"; then
  skip_section "Rust dependency audit" "cargo-audit"
elif has_matching_files "Cargo.toml"; then
  section "Rust dependency audit"
  while IFS= read -r -d '' manifest; do
    dir="$(dirname "$manifest")"
    if [ ! -f "$dir/Cargo.lock" ]; then
      printf 'WARN: %s has no Cargo.lock; cargo audit skipped for this manifest.\n' "${manifest#./}"
    fi
  done < <(matching_files "Cargo.toml")
elif ! has_matching_files "Cargo.lock"; then
  section "Rust dependency audit"
  echo "No Cargo.lock files found."
fi

if ! is_skipped "cargo-audit" && has_matching_files "Cargo.lock"; then
  if command_exists cargo-audit; then
    printf 'Running Rust dependency audits with %s parallel workers across %s lock files.\n' "$AUDIT_JOBS" "$(count_matching_files "Cargo.lock")"
    audit_cargo_locks_parallel || true
  else
    mark_missing "cargo-audit" "cargo install cargo-audit --locked"
  fi
fi

if is_skipped "gitleaks"; then
  skip_section "Gitleaks secret scan" "gitleaks"
elif [ -f ".gitleaks.toml" ]; then
  if command_exists gitleaks; then
    GITLEAKS_ARGS=(
      detect
      --source=.
      --redact
      --no-banner
      --max-target-megabytes "$GITLEAKS_MAX_TARGET_MB"
      --timeout "$GITLEAKS_TIMEOUT"
    )
    if [ "$GITLEAKS_HISTORY" = "0" ]; then
      GITLEAKS_ARGS+=(--no-git)
    fi
    run_simple_check "Gitleaks secret scan" gitleaks "${GITLEAKS_ARGS[@]}"
  else
    mark_missing "gitleaks" "brew install gitleaks"
  fi
fi

if is_skipped "semgrep"; then
  skip_section "Semgrep SAST scan" "semgrep"
elif [ -f ".semgrep.yml" ]; then
  if command_exists semgrep; then
    run_simple_check "Semgrep SAST scan" semgrep scan --config .semgrep.yml
  else
    mark_missing "semgrep" "python -m pip install semgrep"
  fi
fi

if is_skipped "bandit"; then
  skip_section "Bandit Python SAST scan" "bandit"
elif [ -f ".bandit.yaml" ]; then
  if command_exists bandit; then
    BANDIT_TARGETS=()
    for dir in new-platform/app new-platform/scripts scripts; do
      if [ -d "$dir" ]; then
        BANDIT_TARGETS+=("$dir")
      fi
    done
    if [ "${#BANDIT_TARGETS[@]}" -gt 0 ]; then
      printf 'Bandit minimum severity=%s confidence=%s.\n' "$BANDIT_SEVERITY" "$BANDIT_CONFIDENCE"
      run_simple_check "Bandit Python SAST scan" bandit "$(bandit_severity_flag)" "$(bandit_confidence_flag)" -c .bandit.yaml -r "${BANDIT_TARGETS[@]}"
    else
      echo "No Bandit target directories found."
    fi
  else
    mark_missing "bandit" "python -m pip install bandit"
  fi
fi

if is_skipped "checkov"; then
  skip_section "Checkov IaC scan" "checkov"
elif [ -f ".checkov.yaml" ]; then
  if command_exists checkov; then
    run_simple_check "Checkov IaC scan" checkov --config-file .checkov.yaml
  else
    mark_missing "checkov" "python -m pip install checkov"
  fi
fi

if is_skipped "trivy"; then
  skip_section "Trivy filesystem scan" "trivy"
elif command_exists trivy; then
  TRIVY_ARGS=(
    fs
    --scanners "$TRIVY_SCANNERS"
    --parallel "$TRIVY_PARALLEL"
    --severity "HIGH,CRITICAL"
    --exit-code 1
    --ignore-unfixed
  )
  if [ -n "$TRIVY_SKIP_DIRS" ]; then
    TRIVY_ARGS+=(--skip-dirs "$TRIVY_SKIP_DIRS")
  fi
  if [ -n "$TRIVY_SKIP_FILES" ]; then
    TRIVY_ARGS+=(--skip-files "$TRIVY_SKIP_FILES")
  fi
  TRIVY_ARGS+=(.)
  run_simple_check "Trivy filesystem scan" trivy "${TRIVY_ARGS[@]}"
else
  mark_missing "trivy" "brew install trivy"
fi

if [ "$MISSING" -ne 0 ]; then
  echo
  echo "Security audit could not run completely because required tools are missing."
  exit 127
fi

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "Security audit failed."
  exit 1
fi

echo
echo "Security audit passed."
