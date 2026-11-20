# Security Policy

This repository holds the companion code for *The Backend of Luck*. The examples
are written to be read and run in isolated environments. They take every
credential from the environment and ship no real secrets.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
**"Report a vulnerability"** button on the [Security tab](../../security/advisories)
of this repository. This opens a private advisory visible only to the maintainers.

Do not open a public issue for a security report.

We aim to acknowledge a report within a few business days and to agree on a
disclosure timeline once the issue is confirmed.

## Scope

- The code here is illustrative. If an example omits a hardening step that the
  book explains in prose, that is a documentation gap, not a vulnerability.
- Reports about hardcoded secrets, injection, unsafe defaults, or a script that
  is destructive without a confirmation gate are in scope and welcome.

## Commit signing and integrity of `main`

Every change that lands on `main` must be signed and verified, so the history of
this repository can be trusted as a record of who changed what.

- **Contributors** sign every commit with an SSH or GPG key registered on their
  GitHub account. SSH keys must be added as a **Signing Key**, not only as an
  authentication key. Setup is in [CONTRIBUTING.md](CONTRIBUTING.md#signing-your-commits)
  and explained in depth in
  [Chapter 23c](23c-secrets-management-git-hygiene/README.md#commit-and-tag-signing-with-ssh-and-gpg).
- **Enforcement.** `main` requires signed commits through branch protection.
  Pushes and merges that bring in unsigned or unverifiable commits are rejected.
- **Bots.** Dependabot signs its own commits, and squash merges or merge commits
  created by GitHub are signed with GitHub's key. Workflows in this repository do
  not push commits; any future automation that does must create commits through
  the GitHub API as a GitHub App (which GitHub signs) or use its own registered
  signing key.
- **Maintainer review.** Before approving, maintainers check that every commit
  in the pull request shows **Verified** on GitHub, and that the signer is the
  person who opened the pull request. Locally, `git log --show-signature` and
  `git verify-commit <sha>` give the same answer; GitHub's own merge commits need
  its public key first (`curl -fsSL https://github.com/web-flow.gpg | gpg --import`).
  Release tags are signed and checked with `git verify-tag`.
- **Lost or compromised signing key.** Remove it from your GitHub account
  straight away, revoke it if it is a GPG key, and tell the maintainers through
  private reporting (above) so commits signed with it after the loss can be
  reviewed.
