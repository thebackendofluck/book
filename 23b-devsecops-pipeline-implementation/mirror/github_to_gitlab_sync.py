#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Mirror a GitHub repository to a GitLab instance."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path


logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("mirror")

GITHUB_REPO = os.environ["GITHUB_REPO_URL"]
GITLAB_REPO = os.environ["GITLAB_REPO_URL"]
WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/mirror"))


def run(command: list[str], cwd: Path | None = None) -> None:
    LOG.info("running: %s", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def sync() -> None:
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)

    run(["git", "clone", "--mirror", GITHUB_REPO, str(WORK_DIR)])
    run(["git", "remote", "set-url", "--push", "origin", GITLAB_REPO], cwd=WORK_DIR)
    run(["git", "push", "--mirror"], cwd=WORK_DIR)


if __name__ == "__main__":
    sync()
