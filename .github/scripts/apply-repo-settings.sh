#!/usr/bin/env bash
# apply-repo-settings.sh — one-shot, idempotent repository configuration that
# the Dependabot automation needs. Safe to re-run; prints the resulting state.
#
#   1. Allow GitHub Actions to approve pull requests (GITHUB_TOKEN reviews
#      count toward the required approval). For a repository owned by an
#      organization this must ALSO be allowed at organization level; the
#      script tries that first and explains what to do when the token lacks
#      the admin:org scope.
#   2. Allow auto-merge on the repository.
#   3. Make "Dependency Gate" a required status check on `main`, keeping the
#      existing review / conversation-resolution rules.
#   4. Create the labels the workflows use.
#
# Usage: GH_REPO=thebackendofluck/book .github/scripts/apply-repo-settings.sh
set -euo pipefail

REPO="${GH_REPO:-thebackendofluck/book}"
BRANCH="${GH_BRANCH:-main}"
GATE_CONTEXT="${GATE_CONTEXT:-Dependency Gate}"
OWNER="${REPO%%/*}"
STATUS=0

say()  { printf '\033[1;34m[settings]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[settings] WARN\033[0m %s\n' "$*"; }

say "1/4 allow GitHub Actions to create and approve pull requests"
if [ "$(gh api "users/${OWNER}" --jq .type)" = "Organization" ]; then
  if gh api -X PUT "orgs/${OWNER}/actions/permissions/workflow" \
       -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true >/dev/null 2>&1; then
    say "  organization ${OWNER}: enabled"
  else
    warn "  could not change the organization setting (token needs admin:org, or you are not an org admin)."
    warn "  Either run:  gh auth refresh -h github.com -s admin:org   and re-run this script,"
    warn "  or tick 'Allow GitHub Actions to create and approve pull requests' at"
    warn "  https://github.com/organizations/${OWNER}/settings/actions"
  fi
fi
if gh api -X PUT "repos/${REPO}/actions/permissions/workflow" \
     -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true >/dev/null 2>&1; then
  gh api "repos/${REPO}/actions/permissions/workflow"; echo
else
  warn "  repository setting refused (organization policy still blocks it). Auto-approve will not work until it is allowed;"
  warn "  the nightly sweeper merges green Dependabot PRs in the meantime."
  STATUS=1
fi

say "2/4 allow auto-merge + delete head branches after merge"
gh api -X PATCH "repos/${REPO}" \
  -F allow_auto_merge=true \
  -F delete_branch_on_merge=true \
  --jq '{allow_auto_merge, delete_branch_on_merge}'
echo

say "3/4 branch protection on ${BRANCH}: required check '${GATE_CONTEXT}' + 1 review"
# The classic branch-protection PUT replaces the whole object, so every rule
# we want to keep is restated here.
gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<JSON >/dev/null
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["${GATE_CONTEXT}"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
JSON
gh api "repos/${REPO}/branches/${BRANCH}/protection" \
  --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, conversation: .required_conversation_resolution.enabled}'
echo

say "4/4 labels"
ensure_label() {
  local name="$1" color="$2" desc="$3"
  if gh label list -R "$REPO" --json name --jq '.[].name' | grep -Fxq "$name"; then
    gh label edit "$name" -R "$REPO" --color "$color" --description "$desc" >/dev/null
  else
    gh label create "$name" -R "$REPO" --color "$color" --description "$desc" >/dev/null
  fi
  echo "  ${name}"
}
ensure_label dependencies      0366d6 "Dependency updates"
ensure_label security          d73a4a "Fixes a security advisory"
ensure_label needs-human-review fbca04 "Automation declined to merge; a maintainer must look"
ensure_label retest            c5def5 "Re-run the Dependency Gate on this pull request"
ensure_label python            3572A5 "Python ecosystem"
ensure_label javascript        f1e05a "npm ecosystem"
ensure_label go                00ADD8 "Go modules"
ensure_label rust              dea584 "Cargo"
ensure_label docker            0db7ed "Container images"
ensure_label github-actions    2088ff "Workflow actions"

if [ "$STATUS" = 0 ]; then say "done"; else warn "done with the approval setting still pending (see step 1)"; fi
exit "$STATUS"
