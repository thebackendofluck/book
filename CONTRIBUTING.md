# Contributing

Thanks for taking the time to look at the companion code for
*The Backend of Luck*.

## Ground rules

- **Never commit secrets.** Every credential is read from the environment or an
  obvious placeholder. Pull requests are scanned; a real-looking secret will
  block the merge.
- Keep examples runnable and self-contained. If a change needs a service, show
  how to stand it up (compose file, env template) rather than assuming one.
- Match the surrounding style of the chapter you are editing.

## Workflow

1. Fork and branch from `main`.
2. Make your change; run the chapter's own checks where they exist
   (`pytest`, `shellcheck`, `terraform fmt`, linters).
3. Open a pull request describing what you changed and why. One approving review
   is required before merge.

## Reporting problems

- Security issues: see [SECURITY.md](SECURITY.md) (use private reporting).
- Everything else: open an issue.
