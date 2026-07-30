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
Adyen Admin HTTP client.

Replaces the Ruby mechanize-based client with httpx.  The Adyen admin panel
did not expose a skin management API at the time, so this client scrapes the
admin UI.  Authentication is via HTML form submission; session state is kept
in a cookie jar on the underlying httpx.Client.

Key concepts translated from Ruby:
  - Adyen::Admin::Client         → AdyenAdminClient
  - Adyen::Admin.login()         → AdyenAdmin.login() / AdyenAdmin.from_credentials_file()
  - Adyen::Admin::AuthenticationError → AuthenticationError
  - cookie_jar.clear!()          → client.reset_session()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADYEN_TEST_BASE = "https://test.adyen.com"
ADYEN_LIVE_BASE = "https://live.adyen.com"
CA_ADMIN_PATH = "/ca/ca"

_LOGIN_PATH = "/ca/ca/skin/overview.shtml"
_LOGIN_ACTION = "/ca/ca/login.shtml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthenticationError(Exception):
    """Raised when an Adyen admin request fails due to an invalid session."""


# ---------------------------------------------------------------------------
# Credentials model
# ---------------------------------------------------------------------------


class AdyenCredentials(BaseModel):
    """Credentials loaded from credentials.yml (mirrors the Ruby YAML schema)."""

    account: str = Field(..., description="Adyen company / account name")
    user: str = Field(..., description="Admin username")
    password: str = Field(..., description="Admin password")
    test_skin_code: Optional[str] = None
    other_skin_codes: list[str] = Field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> AdyenCredentials:
        """Load credentials from a YAML file (credentials.yml)."""
        data = yaml.safe_load(Path(path).read_text())
        # The Ruby YAML used symbol keys (:account, :user, …); normalise both.
        normalised = {
            str(k).lstrip(":"): v for k, v in data.items()
        }
        return cls(**normalised)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class AdyenAdminClient:
    """
    Low-level HTTP client for the Adyen admin panel.

    Maintains a persistent httpx.Client with cookies so that the session
    obtained during login is reused across subsequent requests.  This mirrors
    the mechanize agent from the Ruby gem.
    """

    def __init__(self, base_url: str = ADYEN_TEST_BASE) -> None:
        self.base_url = base_url.rstrip("/")
        self._authenticated = False
        self._client = httpx.Client(
            base_url=self.base_url,
            follow_redirects=True,
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def reset_session(self) -> None:
        """Clear all cookies and mark the client as unauthenticated."""
        self._client.cookies.clear()
        self._authenticated = False

    def login(self, account: str, user: str, password: str) -> None:
        """
        Authenticate against the Adyen admin panel.

        Submits the login form and checks the response for a redirect to the
        skin overview page (sign of success).  Raises AuthenticationError on
        failure.
        """
        logger.debug("Logging in to Adyen admin as %s/%s", account, user)
        payload = {
            "j_username": f"{account}/{user}",
            "j_password": password,
        }
        resp = self._client.post(_LOGIN_ACTION, data=payload)
        resp.raise_for_status()

        # The Adyen admin panel redirects to the overview page on success and
        # Adyen stays on the login page on failure.  We detect failure by the
        # presence of the login form input in the response body.
        if "j_username" in resp.text:
            self._authenticated = False
            raise AuthenticationError(
                f"Login failed for account={account!r} user={user!r}. "
                "Check credentials."
            )

        self._authenticated = True
        logger.info("Authenticated with Adyen admin as %s/%s", account, user)

    def get(self, path: str, **params: Any) -> httpx.Response:
        """
        Issue a GET request.  Raises AuthenticationError if the session has
        expired (Adyen redirects back to the login page).
        """
        resp = self._client.get(path, params=params)
        self._check_auth(resp)
        resp.raise_for_status()
        return resp

    def post(self, path: str, data: dict[str, Any] | None = None,
             files: dict[str, Any] | None = None) -> httpx.Response:
        """Issue a POST request, raising AuthenticationError on session expiry."""
        resp = self._client.post(path, data=data, files=files)
        self._check_auth(resp)
        resp.raise_for_status()
        return resp

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AdyenAdminClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_auth(self, resp: httpx.Response) -> None:
        """Detect session expiry and raise AuthenticationError."""
        is_login_redirect = (
            "login" in str(resp.url).lower()
            or "j_username" in resp.text
        )
        if is_login_redirect:
            self._authenticated = False
            raise AuthenticationError(
                "Adyen admin session expired or not authenticated. "
                "Call login() first."
            )


# ---------------------------------------------------------------------------
# High-level facade
# ---------------------------------------------------------------------------


class AdyenAdmin:
    """
    Facade that wires credentials + client + skin manager together.

    Mirrors the top-level Adyen::Admin module from the Ruby gem.

    Example:
        admin = AdyenAdmin.from_credentials_file("credentials.yml")
        admin.login()
        skins = admin.skins.all_remote()
    """

    def __init__(self, client: AdyenAdminClient) -> None:
        from adyen_admin.skins import SkinManager  # local import to avoid cycles

        self._client = client
        self.skins = SkinManager(client)

    @classmethod
    def from_credentials(
        cls,
        account: str,
        user: str,
        password: str,
        base_url: str = ADYEN_TEST_BASE,
    ) -> AdyenAdmin:
        client = AdyenAdminClient(base_url=base_url)
        instance = cls(client)
        instance._credentials = AdyenCredentials(
            account=account, user=user, password=password
        )
        return instance

    @classmethod
    def from_credentials_file(
        cls,
        path: str | Path = "credentials.yml",
        base_url: str = ADYEN_TEST_BASE,
    ) -> AdyenAdmin:
        creds = AdyenCredentials.from_file(path)
        return cls.from_credentials(
            account=creds.account,
            user=creds.user,
            password=creds.password,
            base_url=base_url,
        )

    def login(self) -> None:
        creds = self._credentials
        self._client.login(creds.account, creds.user, creds.password)

    @property
    def authenticated(self) -> bool:
        return self._client.authenticated

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AdyenAdmin:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
