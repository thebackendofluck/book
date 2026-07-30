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
