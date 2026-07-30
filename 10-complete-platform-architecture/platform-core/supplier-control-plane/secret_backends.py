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
secret_backends.py
------------------
Production-ready secret backend implementations for the Credential Manager.

Provides:
- EnvVarSecretBackend: reads secrets from environment variables (CI/dev fallback)
- VaultSecretBackend: reads/writes to HashiCorp Vault KV v2 API
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class EnvVarSecretBackend:
    """
    Secret backend that reads from environment variables.

    Path components are converted to env var names:
    suppliers/evo/brands/b1/jurisdictions/GB -> SECRET_SUPPLIERS_EVO_BRANDS_B1_JURISDICTIONS_GB

    The env var value must be a JSON-encoded dict.
    """

    PREFIX = "SECRET_"

    def _env_key(self, path: str) -> str:
        return self.PREFIX + path.upper().replace("/", "_").replace("-", "_")

    def read(self, path: str) -> Optional[dict[str, str]]:
        raw = os.environ.get(self._env_key(path))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to decode env var for path %s", path)
            return None

    def write(self, path: str, value: dict[str, str]) -> None:
        os.environ[self._env_key(path)] = json.dumps(value)

    def delete(self, path: str) -> None:
        os.environ.pop(self._env_key(path), None)


class VaultSecretBackend:
    """
    Secret backend that reads/writes to HashiCorp Vault KV v2 API.

    Parameters
    ----------
    vault_addr:  Vault server address (e.g. https://vault.internal:8200).
    vault_token: Authentication token for Vault.
    mount_path:  KV v2 secrets engine mount (default 'secret').
    session:     Optional HTTP session (e.g. requests.Session).
    """

    def __init__(
        self,
        vault_addr: str,
        vault_token: str,
        mount_path: str = "secret",
        session: object = None,
    ) -> None:
        self._addr = vault_addr.rstrip("/")
        self._token = vault_token
        self._mount = mount_path
        self._session = session

    def _url(self, path: str) -> str:
        return f"{self._addr}/v1/{self._mount}/data/{path}"

    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self._token}

    def read(self, path: str) -> Optional[dict[str, str]]:
        import urllib.request
        url = self._url(path)
        if self._session is not None:
            resp = self._session.get(url, headers=self._headers())
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()["data"]["data"]
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return data["data"]["data"]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def write(self, path: str, value: dict[str, str]) -> None:
        import urllib.request
        url = self._url(path)
        body = json.dumps({"data": value}).encode()
        if self._session is not None:
            self._session.post(url, json={"data": value}, headers=self._headers())
            return
        req = urllib.request.Request(
            url, data=body,
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass

    def delete(self, path: str) -> None:
        import urllib.request
        url = f"{self._addr}/v1/{self._mount}/metadata/{path}"
        if self._session is not None:
            self._session.delete(url, headers=self._headers())
            return
        req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except urllib.error.HTTPError:
            pass
