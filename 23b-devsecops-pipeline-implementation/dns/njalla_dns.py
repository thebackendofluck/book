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

"""Manage DNS records via Njalla API."""

from __future__ import annotations

import argparse
import os
from typing import Literal

import requests


NJALLA_TOKEN = os.environ["NJALLA_API_TOKEN"]
NJALLA_API = "https://njal.la/api/1/"
DOMAIN = os.environ.get("NJALLA_DOMAIN", "acmetocasino.com")


def request(method: str, params: dict) -> dict:
    response = requests.post(
        NJALLA_API,
        json={"method": method, "params": params},
        headers={"Authorization": f"Njalla {NJALLA_TOKEN}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("result", {})


def upsert_record(record_type: Literal["A", "AAAA", "CNAME", "TXT"], name: str, content: str) -> dict:
    return request(
        "add-record",
        {"domain": DOMAIN, "type": record_type, "name": name, "content": content},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create DNS records through Njalla")
    parser.add_argument("type", choices=["A", "AAAA", "CNAME", "TXT"])
    parser.add_argument("name")
    parser.add_argument("content")
    args = parser.parse_args()
    print(upsert_record(args.type, args.name, args.content))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
