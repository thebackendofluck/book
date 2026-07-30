# Git Hooks - Client-Side Code Quality Gates

Client-side git hooks enforcing commit message standards across all casino platform repositories. These hooks ensure every commit references a JIRA ticket, enabling full traceability from code changes to project management.

## Hooks

### pre-commit
Self-updating hook that checks for updates from the central git-hooks repository every 12 hours. Ensures all developers stay on the latest version of the hooks without manual intervention.

### commit-msg
Validates that every commit message contains a JIRA ticket reference. Supports multiple formats:
- Branch name contains ticket: `feature/PLAT-1234-description`
- Commit message prefix: `PLAT-1234 fix login flow`
- JIRA tag in message: `Initial commit for JIRA:PLAT-1234`
- Hash-prefixed: `##PLAT-1234 update config`

## Installation

```bash
# Clone the hooks repo alongside your project
git clone git@github.com:acmetocasino/git-hooks.git ~/.git-hooks

# Configure git to use the hooks
git config --global core.hooksPath ~/.git-hooks
```

## Key Patterns for Book Readers

1. **Self-updating hooks**: The pre-commit hook auto-updates from the central repo
2. **JIRA enforcement**: Ensures every commit is traceable to a ticket
3. **Flexible ticket detection**: Supports ticket in branch name OR commit message
4. **Skip escape hatch**: `SKIP_PRE_COMMIT_CHECK=1` for exceptional cases (rebases, etc.)

## Chapter Reference

Chapter 23: DevSecOps in iGaming
