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

"""Block a specific package version in Nexus OSS via REST API."""

from __future__ import annotations

import argparse
import os

import requests


NEXUS_URL = os.environ["NEXUS_URL"].rstrip("/")
NEXUS_CREDS = (os.environ["NEXUS_ADMIN_USER"], os.environ["NEXUS_ADMIN_PASS"])


def block_component(repository: str, group: str, name: str, version: str) -> None:
    response = requests.get(
        f"{NEXUS_URL}/service/rest/v1/search",
        auth=NEXUS_CREDS,
        params={"repository": repository, "group": group, "name": name, "version": version},
        timeout=30,
    )
    response.raise_for_status()
    for item in response.json().get("items", []):
        component_id = item["id"]
        delete_response = requests.delete(
            f"{NEXUS_URL}/service/rest/v1/components/{component_id}",
            auth=NEXUS_CREDS,
            timeout=30,
        )
        delete_response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove a compromised component from Nexus")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    block_component(args.repository, args.group, args.name, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
