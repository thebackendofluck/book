#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# ci_jira_bridge.sh — CI/CD to Jira integration bridge for iGaming operations
#
# Integrates CI/CD pipelines with Jira by extracting ticket IDs from branch
# names, posting build status, adding deployment comments, transitioning
# tickets, and attaching reports. Works with both GitLab CI and GitHub Actions.
#
# Usage:
#   ci_jira_bridge.sh <command> [options]
#
# Commands:
#   extract-ticket     Extract Jira ticket ID from current branch
#   post-build-status  Post build status to Jira ticket
#   add-deploy-comment Add deployment info as Jira comment
#   transition         Transition ticket on successful deployment
#   attach-report      Attach test/security report to ticket
#
# Environment variables (required):
#   JIRA_SERVER      — Jira instance URL (e.g. https://company.atlassian.net)
#   JIRA_USERNAME    — Jira user email
#   JIRA_API_TOKEN   — Jira API token
#
# Environment variables (auto-detected from CI):
#   CI_COMMIT_BRANCH / GITHUB_HEAD_REF — Branch name
#   CI_COMMIT_SHA / GITHUB_SHA         — Commit SHA
#   CI_PIPELINE_URL / GITHUB_SERVER_URL — Pipeline/workflow URL
#   CI_JOB_STATUS                       — Job status (GitLab)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JIRA_SERVER="${JIRA_SERVER:?JIRA_SERVER environment variable is required}"
JIRA_USERNAME="${JIRA_USERNAME:?JIRA_USERNAME environment variable is required}"
JIRA_API_TOKEN="${JIRA_API_TOKEN:?JIRA_API_TOKEN environment variable is required}"

# Auto-detect CI environment
detect_ci_environment() {
    if [[ -n "${GITLAB_CI:-}" ]]; then
        CI_TYPE="gitlab"
        BRANCH="${CI_COMMIT_BRANCH:-${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:-unknown}}"
        COMMIT_SHA="${CI_COMMIT_SHA:-unknown}"
        PIPELINE_URL="${CI_PIPELINE_URL:-}"
        BUILD_STATUS="${CI_JOB_STATUS:-unknown}"
        REPO_URL="${CI_PROJECT_URL:-}"
    elif [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        CI_TYPE="github"
        BRANCH="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-unknown}}"
        COMMIT_SHA="${GITHUB_SHA:-unknown}"
        PIPELINE_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
        BUILD_STATUS="${BUILD_STATUS:-unknown}"
        REPO_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}"
    else
        CI_TYPE="local"
        BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
        COMMIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
        PIPELINE_URL=""
        BUILD_STATUS="unknown"
        REPO_URL=""
    fi
    export REPO_URL
}

# ---------------------------------------------------------------------------
# Jira API helpers
# ---------------------------------------------------------------------------

jira_api() {
    local method="$1"
    local endpoint="$2"
    shift 2

    curl -s -X "$method" \
        -H "Content-Type: application/json" \
        -u "${JIRA_USERNAME}:${JIRA_API_TOKEN}" \
        "${JIRA_SERVER}/rest/api/2/${endpoint}" \
        "$@"
}

jira_get() {
    jira_api GET "$1"
}

jira_post() {
    local endpoint="$1"
    local data="$2"
    jira_api POST "$endpoint" -d "$data"
}

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

# Extract Jira ticket ID from branch name
# Supports: CASINO-123-fix-rtp, feature/CASINO-456, bugfix/PROJ-789-desc
extract_ticket_id() {
    local branch="${1:-$BRANCH}"
    local ticket_id

    ticket_id=$(echo "$branch" | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1)

    if [[ -z "$ticket_id" ]]; then
        echo ""
        return 1
    fi

    echo "$ticket_id"
}

# Post build status to Jira ticket as a comment
post_build_status() {
    local ticket_id="$1"
    local status="${2:-$BUILD_STATUS}"
    local short_sha="${COMMIT_SHA:0:12}"

    local status_icon
    case "$status" in
        success|passed) status_icon="(/)" ;;
        failed)         status_icon="(x)" ;;
        running)        status_icon="(!)" ;;
        *)              status_icon="(?)" ;;
    esac

    local comment
    comment=$(cat <<COMMENT_EOF
h3. Build Status Update
||Field||Value||
|Status|${status_icon} ${status}|
|Commit|{monospace}${short_sha}{monospace}|
|Branch|${BRANCH}|
|CI System|${CI_TYPE}|
COMMENT_EOF
)

    if [[ -n "$PIPELINE_URL" ]]; then
        comment="${comment}
|Pipeline|[View Build|${PIPELINE_URL}]|"
    fi

    comment="${comment}
|Timestamp|$(date -u '+%Y-%m-%dT%H:%M:%SZ')|"

    local json_comment
    json_comment=$(jq -n --arg body "$comment" '{"body": $body}')

    local result
    result=$(jira_post "issue/${ticket_id}/comment" "$json_comment")

    if echo "$result" | jq -e '.id' > /dev/null 2>&1; then
        echo "Posted build status to ${ticket_id}: ${status}"
    else
        echo "ERROR: Failed to post build status to ${ticket_id}" >&2
        echo "$result" >&2
        return 1
    fi
}

# Add deployment information as a Jira comment
add_deploy_comment() {
    local ticket_id="$1"
    local version="${2:-unknown}"
    local environment="${3:-production}"
    local deployer="${4:-${GITLAB_USER_NAME:-${GITHUB_ACTOR:-ci-system}}}"
    local short_sha="${COMMIT_SHA:0:12}"

    local comment
    comment=$(cat <<DEPLOY_EOF
h3. Deployment Information
||Field||Value||
|Version|${version}|
|Environment|${environment}|
|Commit|{monospace}${short_sha}{monospace}|
|Deployed by|${deployer}|
|Timestamp|$(date -u '+%Y-%m-%dT%H:%M:%SZ')|
DEPLOY_EOF
)

    if [[ -n "$PIPELINE_URL" ]]; then
        comment="${comment}
|Pipeline|[View Pipeline|${PIPELINE_URL}]|"
    fi

    local json_comment
    json_comment=$(jq -n --arg body "$comment" '{"body": $body}')

    local result
    result=$(jira_post "issue/${ticket_id}/comment" "$json_comment")

    if echo "$result" | jq -e '.id' > /dev/null 2>&1; then
        echo "Added deployment comment to ${ticket_id}: v${version} -> ${environment}"
    else
        echo "ERROR: Failed to add deployment comment to ${ticket_id}" >&2
        return 1
    fi
}

# Transition a Jira ticket to a new status
transition_ticket() {
    local ticket_id="$1"
    local target_status="$2"

    # Get available transitions
    local transitions
    transitions=$(jira_get "issue/${ticket_id}/transitions")

    # Find matching transition ID
    local transition_id
    transition_id=$(echo "$transitions" | jq -r \
        --arg status "$target_status" \
        '.transitions[] | select(.name == $status or (.to.name == $status)) | .id' | head -1)

    if [[ -z "$transition_id" ]]; then
        echo "ERROR: Transition to '${target_status}' not available for ${ticket_id}" >&2
        echo "Available transitions:" >&2
        echo "$transitions" | jq -r '.transitions[].name' >&2
        return 1
    fi

    local result
    result=$(jira_post "issue/${ticket_id}/transitions" \
        "{\"transition\": {\"id\": \"${transition_id}\"}}")

    # Transitions return empty body on success (204)
    echo "Transitioned ${ticket_id} -> ${target_status}"
}

# Attach a file (test report, security scan, etc.) to a Jira ticket
attach_report() {
    local ticket_id="$1"
    local file_path="$2"
    local description="${3:-CI/CD report}"

    if [[ ! -f "$file_path" ]]; then
        echo "ERROR: File not found: ${file_path}" >&2
        return 1
    fi

    local result
    result=$(curl -s -X POST \
        -H "X-Atlassian-Token: no-check" \
        -u "${JIRA_USERNAME}:${JIRA_API_TOKEN}" \
        -F "file=@${file_path}" \
        "${JIRA_SERVER}/rest/api/2/issue/${ticket_id}/attachments")

    if echo "$result" | jq -e '.[0].id' > /dev/null 2>&1; then
        local filename
        filename=$(basename "$file_path")
        echo "Attached ${filename} to ${ticket_id}"

        # Add comment about the attachment
        local json_comment
        json_comment=$(jq -n --arg body "Attached report: ${filename} (${description})" \
            '{"body": $body}')
        jira_post "issue/${ticket_id}/comment" "$json_comment" > /dev/null
    else
        echo "ERROR: Failed to attach file to ${ticket_id}" >&2
        echo "$result" >&2
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Composite workflows
# ---------------------------------------------------------------------------

# Full deployment workflow: post status + add comment + transition
full_deploy_workflow() {
    local ticket_id="$1"
    local version="${2:-unknown}"
    local environment="${3:-production}"
    local status="${4:-success}"

    echo "Running full deployment workflow for ${ticket_id}..."

    post_build_status "$ticket_id" "$status"

    if [[ "$status" == "success" || "$status" == "passed" ]]; then
        add_deploy_comment "$ticket_id" "$version" "$environment"

        # Transition based on environment
        case "$environment" in
            production)
                transition_ticket "$ticket_id" "Deployed" || true
                ;;
            staging)
                transition_ticket "$ticket_id" "In Review" || \
                transition_ticket "$ticket_id" "Review" || true
                ;;
        esac
    fi

    echo "Deployment workflow complete for ${ticket_id}"
}

# ---------------------------------------------------------------------------
# Usage / help
# ---------------------------------------------------------------------------

print_usage() {
    cat <<USAGE_EOF
Usage: $(basename "$0") <command> [options]

Commands:
  extract-ticket                         Extract ticket ID from branch name
  post-build-status <ticket> [status]    Post build status to ticket
  add-deploy-comment <ticket> <version> [env] [deployer]
                                         Add deployment info as comment
  transition <ticket> <status>           Transition ticket to new status
  attach-report <ticket> <file> [desc]   Attach file to ticket
  full-deploy <ticket> <version> [env] [status]
                                         Run full deployment workflow

Environment Variables:
  JIRA_SERVER      Jira URL (required)
  JIRA_USERNAME    Jira email (required)
  JIRA_API_TOKEN   Jira API token (required)

Examples:
  # Extract ticket from branch name
  $(basename "$0") extract-ticket

  # Post build result
  $(basename "$0") post-build-status CASINO-123 success

  # Full deployment workflow
  $(basename "$0") full-deploy CASINO-123 2.14.0 production success

  # Attach security scan report
  $(basename "$0") attach-report CASINO-123 security-report.pdf "OWASP ZAP scan"
USAGE_EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    detect_ci_environment

    local command="${1:-help}"
    shift || true

    case "$command" in
        extract-ticket)
            local ticket_id
            ticket_id=$(extract_ticket_id "${1:-}")
            if [[ -n "$ticket_id" ]]; then
                echo "$ticket_id"
            else
                echo "No ticket ID found in branch: ${BRANCH}" >&2
                exit 1
            fi
            ;;
        post-build-status)
            local ticket="${1:?Ticket ID required}"
            local status="${2:-$BUILD_STATUS}"
            post_build_status "$ticket" "$status"
            ;;
        add-deploy-comment)
            local ticket="${1:?Ticket ID required}"
            local version="${2:?Version required}"
            local env="${3:-production}"
            local deployer="${4:-}"
            add_deploy_comment "$ticket" "$version" "$env" "$deployer"
            ;;
        transition)
            local ticket="${1:?Ticket ID required}"
            local target="${2:?Target status required}"
            transition_ticket "$ticket" "$target"
            ;;
        attach-report)
            local ticket="${1:?Ticket ID required}"
            local file="${2:?File path required}"
            local desc="${3:-CI/CD report}"
            attach_report "$ticket" "$file" "$desc"
            ;;
        full-deploy)
            local ticket="${1:?Ticket ID required}"
            local version="${2:?Version required}"
            local env="${3:-production}"
            local status="${4:-success}"
            full_deploy_workflow "$ticket" "$version" "$env" "$status"
            ;;
        help|--help|-h)
            print_usage
            ;;
        *)
            echo "ERROR: Unknown command: ${command}" >&2
            print_usage >&2
            exit 1
            ;;
    esac
}

main "$@"
