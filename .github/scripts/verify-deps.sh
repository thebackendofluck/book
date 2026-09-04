#!/usr/bin/env bash
# verify-deps.sh — install and test the projects whose dependency manifests
# changed, one directory at a time, in an isolated environment.
#
# Used by two callers with the same semantics:
#   * .github/workflows/dependency-gate.yml (GitHub-hosted runner, per PR)
#   * the nightly sweeper on the maintainer's server (same script, same rules)
#
# Modes
#   verify-deps.sh --list [--base <ref>]      print JSON matrix of {dir, ecosystem}
#                                              for manifests changed since <ref>
#   verify-deps.sh [--base <ref>] [DIR ...]   verify the given DIRs (or the
#                                              changed ones when no DIR is given)
#
# Exit status: 0 when every verified directory passed, 1 otherwise.
#
# Environment
#   VERIFY_BASE            default base ref (fallback for --base), default origin/main
#   VERIFY_TIMEOUT         per-step timeout in seconds (default 900)
#   VERIFY_TESTS_MODE      strict (default) | report — in report mode a failing
#                          test suite is logged but does not fail the run
#   VERIFY_DOCKER_BUILD    1 to actually `docker build` Dockerfile changes
#   VERIFY_SKIP_TESTS_FILE path to a newline list of dirs whose test suites need
#                          external services (they are installed and built, tests
#                          run in report mode). Default: .github/deps-verify-skip-tests.txt
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
if [ -n "${VERIFY_ROOT:-}" ]; then
  REPO_ROOT="$VERIFY_ROOT"
elif ! REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi
cd "$REPO_ROOT"

VERIFY_BASE="${VERIFY_BASE:-origin/main}"
# When a directory fails, re-run the same verification on the merge-base with
# VERIFY_BASE. A failure that already exists there is pre-existing breakage
# unrelated to the change and is reported, not blocking. Set to "none" to
# disable (the baseline run itself uses "none").
VERIFY_BASELINE="${VERIFY_BASELINE:-auto}"
VERIFY_TIMEOUT="${VERIFY_TIMEOUT:-900}"
VERIFY_TESTS_MODE="${VERIFY_TESTS_MODE:-strict}"
VERIFY_DOCKER_BUILD="${VERIFY_DOCKER_BUILD:-0}"
VERIFY_SKIP_TESTS_FILE="${VERIFY_SKIP_TESTS_FILE:-.github/deps-verify-skip-tests.txt}"
case "$VERIFY_SKIP_TESTS_FILE" in /*) ;; *) VERIFY_SKIP_TESTS_FILE="$REPO_ROOT/$VERIFY_SKIP_TESTS_FILE" ;; esac

MODE="verify"
BASE="$VERIFY_BASE"
DIRS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --list) MODE="list"; shift ;;
    --base) BASE="$2"; shift 2 ;;
    --base=*) BASE="${1#--base=}"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) DIRS+=("${1%/}"); shift ;;
  esac
done

log()  { printf '\033[1;34m[verify]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[verify] WARN\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[verify] FAIL\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------
# Returns the ecosystem for a manifest path, or nothing when not a manifest.
ecosystem_of() {
  local base
  base="$(basename "$1")"
  case "$base" in
    requirements*.txt|pyproject.toml|poetry.lock|uv.lock|Pipfile|Pipfile.lock|setup.py|setup.cfg) echo python ;;
    package.json|package-lock.json|pnpm-lock.yaml|yarn.lock|npm-shrinkwrap.json) echo node ;;
    go.mod|go.sum) echo go ;;
    Cargo.toml|Cargo.lock) echo rust ;;
    Dockerfile|Dockerfile.*|*.Dockerfile) echo docker ;;
    *)
      case "$1" in
        .github/workflows/*.yml|.github/workflows/*.yaml) echo actions ;;
      esac
      ;;
  esac
}

# Lists "dir<TAB>ecosystem" for every manifest changed between BASE and HEAD.
changed_manifests() {
  local merge_base
  if ! merge_base="$(git merge-base "$BASE" HEAD 2>/dev/null)"; then
    warn "cannot compute merge-base with $BASE; diffing against HEAD~1"
    merge_base="HEAD~1"
  fi
  git diff --name-only "$merge_base" HEAD -- | while IFS= read -r f; do
    eco="$(ecosystem_of "$f")"
    [ -z "$eco" ] && continue
    dir="$(dirname "$f")"
    [ "$eco" = "actions" ] && dir=".github/workflows"
    printf '%s\t%s\n' "$dir" "$eco"
  done | sort -u
}

any_exists() {
  local f
  for f in "$@"; do [ -e "$f" ] && return 0; done
  return 1
}

# Lists every ecosystem present in a directory (a dir may hold several).
ecosystems_in_dir() {
  local d="$1"
  [ "$d" = ".github/workflows" ] && { echo actions; return; }
  any_exists "$d"/requirements*.txt "$d"/pyproject.toml "$d"/Pipfile "$d"/setup.py && echo python
  [ -f "$d/package.json" ] && echo node
  [ -f "$d/go.mod" ] && echo go
  [ -f "$d/Cargo.toml" ] && echo rust
  any_exists "$d"/Dockerfile "$d"/Dockerfile.* "$d"/*.Dockerfile && echo docker
  return 0
}

emit_matrix_json() {
  # stdin: dir<TAB>ecosystem lines → JSON array of objects (no jq dependency)
  python3 -c '
import json, sys
rows = []
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    d, e = line.split("\t", 1)
    rows.append({"dir": d, "ecosystem": e})
print(json.dumps(rows))
'
}

if [ "$MODE" = "list" ]; then
  changed_manifests | emit_matrix_json
  exit 0
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
run_step() {
  # run_step <label> <cmd...>  — runs in the current dir with a timeout
  local label="$1"; shift
  log "→ $label: $*"
  if timeout --foreground "$VERIFY_TIMEOUT" "$@"; then
    return 0
  else
    local rc=$?
    fail "$label exited $rc"
    return "$rc"
  fi
}

tests_mode_for() {
  local d="$1"
  if [ -f "$VERIFY_SKIP_TESTS_FILE" ] && grep -Fxq "$d" "$VERIFY_SKIP_TESTS_FILE"; then
    echo report
  else
    echo "$VERIFY_TESTS_MODE"
  fi
}

run_tests() {
  # run_tests <dir> <cmd...> — honours strict/report mode
  local d="$1"; shift
  local mode
  mode="$(tests_mode_for "$d")"
  if run_step "tests" "$@"; then
    return 0
  fi
  if [ "$mode" = "report" ]; then
    warn "tests failed in $d but mode=report (needs external services); not blocking"
    return 0
  fi
  return 1
}

need() { command -v "$1" >/dev/null 2>&1 || { fail "missing tool: $1"; return 1; }; }

# ---------------------------------------------------------------------------
# Ecosystem verifiers — each runs inside the project directory
# ---------------------------------------------------------------------------
has_python_tests() {
  local f
  for f in tests test test_*.py ./*_test.py; do
    [ -e "$f" ] && return 0
  done
  return 1
}

verify_python() {
  local d="$1"
  need python3 || return $?
  local venv=".venv-verify"
  rm -rf "$venv"
  local py="$venv/bin/python"
  local -a PIP
  if command -v uv >/dev/null 2>&1; then
    run_step "venv" uv venv -q "$venv" || return $?
    PIP=(uv pip install -q --python "$py")
  else
    run_step "venv" python3 -m venv "$venv" || return $?
    PIP=("$py" -m pip install -q)
  fi

  if [ -f poetry.lock ]; then
    # Poetry project: honour the lockfile exactly.
    run_step "install poetry" "${PIP[@]}" poetry || return $?
    run_step "poetry check" env POETRY_VIRTUALENVS_CREATE=false "$venv/bin/poetry" check --lock || return $?
    run_step "poetry install" env POETRY_VIRTUALENVS_CREATE=false "$venv/bin/poetry" install --no-root --no-interaction --no-ansi || return $?
  elif [ -f uv.lock ]; then
    need uv || return $?
    run_step "uv sync" env UV_PROJECT_ENVIRONMENT="$venv" uv sync --frozen --no-install-project || return $?
  else
    local installed=0
    for req in requirements*.txt; do
      [ -f "$req" ] || continue
      run_step "pip install $req" "${PIP[@]}" -r "$req" || return $?
      installed=1
    done
    if [ "$installed" = 0 ] && [ -f pyproject.toml ]; then
      run_step "pip install ." "${PIP[@]}" -e . || return $?
    fi
  fi

  # Resolution sanity check: broken requirement graphs show up here.
  if command -v uv >/dev/null 2>&1; then
    run_step "uv pip check" uv pip check --python "$py" || return $?
  elif "$py" -m pip --version >/dev/null 2>&1; then
    run_step "pip check" "$py" -m pip check || return $?
  fi

  if has_python_tests; then
    run_step "install pytest" "${PIP[@]}" pytest || return $?
    run_tests "$d" "$py" -m pytest -q -x -p no:cacheprovider || return $?
  else
    log "no test suite in $d; install + resolution check only"
  fi
}

has_npm_script() {
  node -e 'const s=require("./package.json").scripts||{}; process.exit(s[process.argv[1]]?0:1)' "$1"
}

verify_node() {
  local d="$1" pm
  need node || return $?
  export CI=true
  export NODE_ENV=development
  if [ -f pnpm-lock.yaml ]; then
    pm=pnpm
    command -v pnpm >/dev/null 2>&1 || corepack enable >/dev/null 2>&1 || true
    need pnpm || return $?
    run_step "pnpm install" pnpm install --frozen-lockfile --ignore-scripts || return $?
  elif [ -f yarn.lock ]; then
    pm=yarn
    command -v yarn >/dev/null 2>&1 || corepack enable >/dev/null 2>&1 || true
    need yarn || return $?
    run_step "yarn install" yarn install --frozen-lockfile --ignore-scripts --non-interactive || return $?
  else
    pm=npm
    need npm || return $?
    if [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then
      run_step "npm ci" npm ci --no-audit --no-fund --ignore-scripts || return $?
    else
      warn "no lockfile in $d — npm install (non-reproducible)"
      run_step "npm install" npm install --no-audit --no-fund --ignore-scripts || return $?
    fi
  fi
  if has_npm_script build; then
    run_step "build" "$pm" run build || return $?
  fi
  if has_npm_script test; then
    run_tests "$d" "$pm" run test || return $?
  else
    log "no test script in $d; install + build only"
  fi
}

verify_go() {
  local d="$1"
  need go || return $?
  export GOFLAGS=-mod=mod GOTOOLCHAIN=auto
  run_step "go mod download" go mod download || return $?
  run_step "go build" go build ./... || return $?
  run_step "go vet" go vet ./... || return $?
  if ls ./*_test.go >/dev/null 2>&1 || find . -name '*_test.go' -print -quit | grep -q .; then
    run_tests "$d" go test ./... || return $?
  else
    log "no Go tests in $d"
  fi
}

verify_rust() {
  local d="$1"
  need cargo || return $?
  local locked=()
  [ -f Cargo.lock ] && locked=(--locked)
  run_step "cargo build" cargo build "${locked[@]}" || return $?
  run_tests "$d" cargo test "${locked[@]}" || return $?
}

verify_docker() {
  local d="$1"
  local rc=0
  for df in Dockerfile Dockerfile.* ./*.Dockerfile; do
    [ -f "$df" ] || continue
    if command -v hadolint >/dev/null 2>&1; then
      run_step "hadolint $df" hadolint --failure-threshold error "$df" || rc=1
    fi
    if [ "$VERIFY_DOCKER_BUILD" = "1" ] && command -v docker >/dev/null 2>&1; then
      run_step "docker build $df" docker build --pull -f "$df" -t "verify-$(basename "$d" | tr -c 'a-z0-9' '-'):tmp" . || rc=1
    else
      log "docker build skipped for $df (set VERIFY_DOCKER_BUILD=1 to build)"
    fi
  done
  return "$rc"
}

verify_actions() {
  if command -v actionlint >/dev/null 2>&1; then
    run_step "actionlint" actionlint || return $?
  else
    log "actionlint not installed; workflow syntax not linted"
  fi
}

# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------
baseline_also_fails() {
  # baseline_also_fails <dir> <eco> — true when the same verification fails on
  # the merge-base too (so the change under review did not cause it).
  local d="$1" eco="$2" mb wt rc
  mb="$(git merge-base "$BASE" HEAD 2>/dev/null)" || { warn "no merge-base with $BASE; cannot compare"; return 1; }
  if git diff --quiet "$mb" HEAD -- "$d" 2>/dev/null; then
    warn "$d is unchanged since $mb; failure is environmental"; return 1
  fi
  wt="$(mktemp -d "${TMPDIR:-/tmp}/verify-base.XXXXXX")"
  git worktree add -q --detach "$wt" "$mb" || { rm -rf "$wt"; return 1; }
  warn "$d failed; re-running on base ${mb:0:10} to check whether it is pre-existing"
  if [ -d "$wt/$d" ]; then
    (cd "$wt/$d" && VERIFY_ROOT="$wt" VERIFY_BASELINE=none "verify_$eco" "$d") && rc=0 || rc=$?
  else
    rc=1  # new directory on this branch: nothing to compare against
  fi
  git worktree remove -f "$wt" >/dev/null 2>&1 || rm -rf "$wt"
  git worktree prune >/dev/null 2>&1 || true
  if [ "${rc:-1}" = 0 ]; then
    fail "$d passes on base but fails with this change → regression"
    return 1
  fi
  warn "$d also fails on base → pre-existing, not caused by this change"
  return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [ ${#DIRS[@]} -eq 0 ]; then
  while IFS=$'\t' read -r dir _; do
    [ -n "$dir" ] && DIRS+=("$dir")
  done < <(changed_manifests)
  # de-duplicate
  if [ ${#DIRS[@]} -gt 0 ]; then
    mapfile -t DIRS < <(printf '%s\n' "${DIRS[@]}" | sort -u)
  fi
fi

if [ ${#DIRS[@]} -eq 0 ]; then
  log "no dependency manifests changed; nothing to verify"
  exit 0
fi

overall=0
declare -a SUMMARY=()
for d in "${DIRS[@]}"; do
  if [ ! -d "$d" ]; then
    warn "$d no longer exists (deleted in this change); skipping"
    SUMMARY+=("SKIP  $d (deleted)")
    continue
  fi
  ecos="$(ecosystems_in_dir "$d")"
  if [ -z "$ecos" ]; then
    warn "$d has no recognised manifest; skipping"
    SUMMARY+=("SKIP  $d (no manifest)")
    continue
  fi
  for eco in $ecos; do
    log "=== $d [$eco] ==="
    start=$(date +%s)
    rc=0
    (cd "$d" && "verify_$eco" "$d") || rc=$?
    if [ "$rc" = 0 ]; then
      SUMMARY+=("PASS  $d [$eco] $(( $(date +%s) - start ))s")
    elif [ "$rc" = 124 ] || [ "$rc" = 126 ] || [ "$rc" = 127 ]; then
      SUMMARY+=("FAIL  $d [$eco] $(( $(date +%s) - start ))s (exit $rc: timeout or missing tool; environment problem)")
      overall=1
    elif [ "$VERIFY_BASELINE" != "none" ] && baseline_also_fails "$d" "$eco"; then
      SUMMARY+=("PASS* $d [$eco] $(( $(date +%s) - start ))s (fails identically on base; pre-existing)")
    else
      SUMMARY+=("FAIL  $d [$eco] $(( $(date +%s) - start ))s")
      overall=1
    fi
    # never leave build artefacts behind for the next directory
    rm -rf "$d/.venv-verify" "$d/node_modules" 2>/dev/null || true
  done
done

echo
echo "==================== dependency verification summary ===================="
printf '%s\n' "${SUMMARY[@]}"
echo "=========================================================================="
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Dependency verification"
    echo
    echo '```'
    printf '%s\n' "${SUMMARY[@]}"
    echo '```'
  } >> "$GITHUB_STEP_SUMMARY"
fi
exit "$overall"
