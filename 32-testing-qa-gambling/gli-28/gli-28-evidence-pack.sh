#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# GLI-28 v1.0 — bundle evidence into a signed tarball for the GLI submission.
#
# Inputs (env vars):
#   DISCLOSURE_REPORT  JSON output of gli-28-disclosure-check.py
#   DRIFT_CSV          CSV output of gli-28-counter-drift.py
#   AXE_DIR            directory of axe-core JSONs (gli-28-a11y.sh)
#   SCREENSHOT_DIR     directory of per-game screenshots
#   GPG_SIGNING_KEY    GPG key id used to sign the tarball
#
# Output:
#   gli-28-evidence-<UTC-DATE>.tar.gz
#   gli-28-evidence-<UTC-DATE>.tar.gz.asc   (detached GPG signature)
#
# Exit codes:
#   0  pack + signature produced
#   2  config / dependency error
#   3  signing failure

set -euo pipefail

: "${DISCLOSURE_REPORT:?DISCLOSURE_REPORT required}"
: "${DRIFT_CSV:?DRIFT_CSV required}"
: "${AXE_DIR:?AXE_DIR required}"
: "${SCREENSHOT_DIR:?SCREENSHOT_DIR required}"
: "${GPG_SIGNING_KEY:?GPG_SIGNING_KEY required}"

for f in "$DISCLOSURE_REPORT" "$DRIFT_CSV"; do
    if [[ ! -r "$f" ]]; then
        echo "error: cannot read $f" >&2
        exit 2
    fi
done
for d in "$AXE_DIR" "$SCREENSHOT_DIR"; do
    if [[ ! -d "$d" ]]; then
        echo "error: directory missing: $d" >&2
        exit 2
    fi
done

if ! command -v gpg >/dev/null 2>&1; then
    echo "error: gpg not installed" >&2
    exit 2
fi

date_tag=$(date -u +%Y-%m-%d)
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

stage="${work_dir}/gli-28-evidence-${date_tag}"
mkdir -p "$stage"
cp "$DISCLOSURE_REPORT" "${stage}/disclosure-report.json"
cp "$DRIFT_CSV"         "${stage}/counter-drift.csv"
cp -r "$AXE_DIR"        "${stage}/axe-reports"
cp -r "$SCREENSHOT_DIR" "${stage}/screenshots"

cat > "${stage}/MANIFEST.txt" <<EOF
GLI-28 v1.0 evidence pack
Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Standard: GLI-28 v1.0 Player User Interface Systems
Contents:
  disclosure-report.json    output of gli-28-disclosure-check.py
  counter-drift.csv         output of gli-28-counter-drift.py
  axe-reports/              per-game axe-core JSONs (WCAG 2.1 AA)
  screenshots/              per-game UI screenshots
EOF

out="gli-28-evidence-${date_tag}.tar.gz"
tar -C "$work_dir" -czf "$out" "$(basename "$stage")"

if ! gpg --batch --yes --local-user "$GPG_SIGNING_KEY" --detach-sign --armor "$out"; then
    echo "FAIL: gpg signing failed" >&2
    exit 3
fi

echo "OK: $out + $out.asc"
exit 0
