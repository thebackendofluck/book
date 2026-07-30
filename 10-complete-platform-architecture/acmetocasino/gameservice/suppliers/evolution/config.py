# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.suppliers.evolution.config — Evolution Gaming Configuration
========================================================================

Evolution requires three sets of credentials:

* ``casino_key``  — The operator's unique identifier in Evolution's system.
* ``api_token``   — Secret token for authenticating outbound API calls.
* ``webhook_secret`` — HMAC-SHA256 secret for verifying inbound push events.

Additionally, Evolution operates separate ``staging`` and ``production``
environments; the ``environment`` field controls which base URL is used.

Credential sources
------------------
All secrets should be loaded from a secrets vault (AWS Secrets Manager,
HashiCorp Vault, etc.) and injected via :meth:`SupplierSettingsManager.update_config`
at startup.  Never store real credentials in source code.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from acmetocasino.gameservice.suppliers.settings import SupplierConfig

_ENV_URLS = {
    "production": "https://api.evo-services.com",
    "staging": "https://api.staging.evo-services.com",
}


class EvolutionConfig(SupplierConfig):
    """Evolution-specific configuration extending the base SupplierConfig.

    Attributes
    ----------
    casino_key:
        Operator-unique key assigned by Evolution.
    api_token:
        Authentication token for outbound API calls to Evolution.
    webhook_secret:
        HMAC-SHA256 shared secret for verifying inbound push events.
    environment:
        ``"production"`` or ``"staging"``.  Controls the API base URL.
    table_config_url:
        URL of the Evolution table configuration feed (updated nightly).
    """

    casino_key: str = Field(default="", description="Evolution operator casino key.")
    api_token: str = Field(default="", description="Evolution API authentication token.")
    webhook_secret: str = Field(
        default="",
        description="HMAC-SHA256 secret for inbound event verification.",
    )
    environment: str = Field(
        default="staging",
        description='Evolution environment: "production" or "staging".',
    )
    table_config_url: str = Field(
        default="",
        description="URL to fetch live table configuration and availability.",
    )

    @field_validator("environment")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        if v not in _ENV_URLS:
            raise ValueError(
                f"evolution environment must be one of {list(_ENV_URLS)!r}, got {v!r}"
            )
        return v

    @property
    def resolved_api_url(self) -> str:
        """Return the API base URL for the configured environment."""
        return self.api_base_url or _ENV_URLS[self.environment]


__all__ = ["EvolutionConfig"]
