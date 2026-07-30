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

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 1

FAILED=0

fail() {
  FAILED=1
  printf 'FAIL: %s\n' "$1"
}

pass() {
  printf 'OK: %s\n' "$1"
}

require_command() {
  local cmd="$1"
  local install_hint="$2"

  if command -v "$cmd" >/dev/null 2>&1; then
    pass "$cmd found at $(command -v "$cmd")"
  else
    fail "$cmd not found. Install: $install_hint"
  fi
}

version_ge() {
  local actual="$1"
  local expected="$2"
  local actual_major actual_minor actual_patch
  local expected_major expected_minor expected_patch

  IFS=. read -r actual_major actual_minor actual_patch <<<"$actual"
  IFS=. read -r expected_major expected_minor expected_patch <<<"$expected"

  actual_major="${actual_major:-0}"
  actual_minor="${actual_minor:-0}"
  actual_patch="${actual_patch:-0}"
  expected_major="${expected_major:-0}"
  expected_minor="${expected_minor:-0}"
  expected_patch="${expected_patch:-0}"

  if [ "$actual_major" -ne "$expected_major" ]; then
    [ "$actual_major" -gt "$expected_major" ]
    return
  fi

  if [ "$actual_minor" -ne "$expected_minor" ]; then
    [ "$actual_minor" -gt "$expected_minor" ]
    return
  fi

  [ "$actual_patch" -ge "$expected_patch" ]
}

require_command trivy "brew install trivy, apt install trivy, or use aquasec/trivy:<version> in CI"
require_command pip-audit "pipx install pip-audit"
require_command gitleaks "brew install gitleaks"
require_command semgrep "pipx install semgrep"
require_command bandit "pipx install bandit"
require_command checkov "pipx install checkov"

if command -v trivy >/dev/null 2>&1; then
  TRIVY_VERSION="$(trivy --version | awk '/^Version:/ {print $2; exit}')"
  REQUIRED_TRIVY_VERSION="${REQUIRED_TRIVY_VERSION:-0.70.0}"

  if version_ge "$TRIVY_VERSION" "$REQUIRED_TRIVY_VERSION"; then
    pass "trivy version $TRIVY_VERSION >= $REQUIRED_TRIVY_VERSION"
  else
    fail "trivy version $TRIVY_VERSION is older than $REQUIRED_TRIVY_VERSION"
  fi
fi

if grep -R -n --include='requirements*.txt' \
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=venv \
  '^trivy==' . >/tmp/trivy-requirements.txt; then
  fail "Trivy is pinned in Python requirements. Trivy is a CLI/container, not a PyPI dependency:"
  sed 's/^/  /' /tmp/trivy-requirements.txt
else
  pass "no trivy== pins found in requirements files"
fi

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "Tool version verification failed."
  exit 1
fi

echo
echo "Tool version verification passed."
