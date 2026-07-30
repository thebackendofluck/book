# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Credential management utilities.

Provides helpers for loading, validating, and filtering Adyen admin
credentials from YAML config files.  Mirrors the credential-filtering logic
in the Ruby spec helper (spec/spec_helper.rb) and the credentials.yml.example
schema.

The YAML schema (compatible with the Ruby gem):

    account: AcmetoCasinoAccount
    user: DummyUser
    password: Password
    test_skin_code: qaJKoAMQ
    other_skin_codes:
      - xxx1
      - cccc2

Ruby symbol keys (:account, :user, …) are also accepted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from adyen_admin.client import AdyenCredentials

__all__ = ["AdyenCredentials", "load_credentials", "filter_sensitive_data"]


def load_credentials(path: str | Path | None = None) -> AdyenCredentials:
    """
    Load credentials from a YAML file.

    Search order when path is None:
      1. credentials.yml  (current directory)
      2. credentials.yml.example  (current directory)

    Raises FileNotFoundError if neither file exists.
    """
    candidates = [path] if path else ["credentials.yml", "credentials.yml.example"]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            return AdyenCredentials.from_file(p)
    raise FileNotFoundError(
        "No credentials file found.  Expected credentials.yml or "
        "credentials.yml.example in the current directory."
    )


def filter_sensitive_data(
    creds: AdyenCredentials,
) -> dict[str, str]:
    """
    Return a mapping of placeholder → real value for use in test cassette
    filtering.  Mirrors the VCR filter_sensitive_data block in spec_helper.rb.

    Example output:
        {
            "<account>": "AcmetoCasinoAccount",
            "<user>": "DummyUser",
            "<password>": "Password",
            "<test_skin_code>": "qaJKoAMQ",
            "<other_skin_codes-0>": "xxx1",
            "<other_skin_codes-1>": "cccc2",
        }
    """
    filters: dict[str, str] = {}
    scalar_fields = ["account", "user", "password", "test_skin_code"]
    for field in scalar_fields:
        value = getattr(creds, field, None)
        if value:
            filters[f"<{field}>"] = value

    for idx, code in enumerate(creds.other_skin_codes):
        filters[f"<other_skin_codes-{idx}>"] = code

    return filters
