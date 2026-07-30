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
adyen_admin — Adyen payment skin management library.

Replaces the adyen-admin Ruby gem (mechanize + rubyzip) with a pure-Python
implementation using httpx for HTTP and the stdlib zipfile for ZIP handling.

Usage:

    from adyen_admin import AdyenAdmin

    admin = AdyenAdmin.from_credentials_file("credentials.yml")
    admin.login()

    skins = admin.skins.all_remote()
    admin.skins.download(skins[0])
    admin.skins.upload("/path/to/skin-ABCD1234")
"""

from __future__ import annotations

from adyen_admin.client import AdyenAdmin, AdyenAdminClient, AuthenticationError
from adyen_admin.skins import Skin, SkinManager

__all__ = [
    "AdyenAdmin",
    "AdyenAdminClient",
    "AuthenticationError",
    "Skin",
    "SkinManager",
]

__version__ = "0.0.18"
