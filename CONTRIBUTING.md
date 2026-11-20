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
- **Sign your commits.** Every commit in a pull request must carry a verified
  SSH or GPG signature. See [Signing your commits](#signing-your-commits).

## Workflow

1. Fork and branch from `main`.
2. Make your change; run the chapter's own checks where they exist
   (`pytest`, `shellcheck`, `terraform fmt`, linters). Commit with signing
   turned on.
3. Open a pull request describing what you changed and why. One approving review
   is required before merge.

## Signing your commits

Git takes the author name and email from local config, so anyone can commit as
anyone. A signature proves the commit came from someone holding a key that is
registered on your GitHub account. `main` will require signed commits, so a pull
request with unsigned commits cannot be merged.

SSH signing is the quickest setup (git 2.34 or later):

```bash
ssh-keygen -t ed25519 -C "you@example.com" -f ~/.ssh/id_ed25519_signing
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519_signing.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true
ssh-add ~/.ssh/id_ed25519_signing
```

Then add the public key on GitHub under **Settings → SSH and GPG keys → New SSH
key** and set **Key type** to **Signing Key**. A key added only as an
authentication key does not verify commits. With the CLI:
`gh ssh-key add ~/.ssh/id_ed25519_signing.pub --type signing`.

GPG works too: set `user.signingkey` to your key ID, keep `commit.gpgsign true`,
and upload the public key as a GPG key. A hardware key (`ed25519-sk` on a
YubiKey) is welcome and recommended. The full walkthrough, including GPG,
hardware keys and local verification, is in
[Chapter 23c: Commit and Tag Signing with SSH and GPG](23c-secrets-management-git-hygiene/README.md#commit-and-tag-signing-with-ssh-and-gpg).

Check before you push. With SSH keys, git only trusts signatures from keys
listed in an `allowed_signers` file, so add your own key there once:

```bash
mkdir -p ~/.config/git
echo "you@example.com namespaces=\"git\" $(cat ~/.ssh/id_ed25519_signing.pub)" >> ~/.config/git/allowed_signers
git config --global gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
```

Then each commit on your branch should show `G`:

```bash
git log --format='%h %G? %s' origin/main..HEAD
```

If you already pushed unsigned commits, re-sign the branch and force-push it:

```bash
git rebase --exec 'git commit --amend --no-edit -S' origin/main
git push --force-with-lease
```

Turning on vigilant mode (**Settings → SSH and GPG keys → Flag unsigned commits
as unverified**) is a good idea once you sign everything.

### Bot commits and merges

- **Dependabot** signs its own commits, and GitHub signs the squash commit it
  creates on merge, so dependency updates and their auto-merge keep working.
- **Squash merges and merge commits made on GitHub** are signed with GitHub's
  own key and show as Verified. GitHub still checks the commits on your branch,
  so unsigned commits there block the merge even though the final squash commit
  would be signed.
- **Rebase merges** will be turned off on `main`, because GitHub cannot sign
  the commits it rewrites. Use squash or a merge commit.

## Reporting problems

- Security issues: see [SECURITY.md](SECURITY.md) (use private reporting).
- Everything else: open an issue.
