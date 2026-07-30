# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Hash utility for user ID anonymization.

Spelpaus (Swedish national self-exclusion registry) requires MD5-hashed
user IDs rather than plain-text identifiers. This protects user privacy
while still allowing the registry to match against its own hashed records.
"""

import hashlib


def generate_md5(text: str) -> str:
    """Generate MD5 hash of a string (UTF-8 encoded)."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def generate_md5_bytes(data: bytes) -> str:
    """Generate MD5 hash of raw bytes."""
    return hashlib.md5(data).hexdigest()
