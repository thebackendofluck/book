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
Tests for AdyenAdminClient and AdyenAdmin.

Mirrors the Ruby spec/adyen-admin/client_spec.rb behaviour:
  - login succeeds with correct credentials
  - login fails with wrong credentials (AuthenticationError)
  - unauthenticated GET raises AuthenticationError
  - authenticated flag transitions correctly

All HTTP calls are intercepted with pytest-httpx — no real network requests.
"""

from __future__ import annotations

import os
import sys

import pytest

# Gate the whole module on pytest-httpx so a full-repo pytest run
# without the chapter-36 dev dependencies installed skips cleanly
# instead of erroring during collection.
pytest_httpx = pytest.importorskip("pytest_httpx")
HTTPXMock = pytest_httpx.HTTPXMock

# Put the package root on sys.path so the tests run regardless of the
# current working directory (the adyen_admin package lives one level up).
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from adyen_admin.client import (
    AdyenAdmin,
    AdyenAdminClient,
    AdyenCredentials,
    AuthenticationError,
    ADYEN_TEST_BASE,
    _LOGIN_ACTION,
    _LOGIN_PATH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> AdyenAdminClient:
    return AdyenAdminClient(base_url=ADYEN_TEST_BASE)


@pytest.fixture
def credentials() -> AdyenCredentials:
    return AdyenCredentials(
        account="AcmetoCasinoAccount",
        user="DummyUser",
        password="Password",
        test_skin_code="qaJKoAMQ",
        other_skin_codes=["xxx1", "cccc2"],
    )


# ---------------------------------------------------------------------------
# AdyenCredentials
# ---------------------------------------------------------------------------


class TestAdyenCredentials:
    def test_from_dict_with_string_keys(self) -> None:
        creds = AdyenCredentials(account="Acme", user="u", password="p")
        assert creds.account == "Acme"
        assert creds.user == "u"

    def test_other_skin_codes_defaults_to_empty_list(self) -> None:
        creds = AdyenCredentials(account="Acme", user="u", password="p")
        assert creds.other_skin_codes == []

    def test_from_file(self, tmp_path) -> None:
        yml = tmp_path / "credentials.yml"
        yml.write_text(
            "account: TestAccount\nuser: testuser\npassword: secret\n"
            "test_skin_code: ABCD1234\nother_skin_codes:\n  - EFGH5678\n"
        )
        creds = AdyenCredentials.from_file(yml)
        assert creds.account == "TestAccount"
        assert creds.test_skin_code == "ABCD1234"
        assert creds.other_skin_codes == ["EFGH5678"]

    def test_from_file_with_ruby_symbol_keys(self, tmp_path) -> None:
        """Accept :account style keys from old Ruby-generated YAML."""
        yml = tmp_path / "credentials.yml"
        yml.write_text(":account: SymbolAccount\n:user: u\n:password: p\n")
        creds = AdyenCredentials.from_file(yml)
        assert creds.account == "SymbolAccount"


# ---------------------------------------------------------------------------
# AdyenAdminClient — authentication
# ---------------------------------------------------------------------------


class TestAdyenAdminClientLogin:
    def test_login_succeeds_with_correct_credentials(
        self, client: AdyenAdminClient, credentials: AdyenCredentials,
        httpx_mock: HTTPXMock,
    ) -> None:
        # Simulate successful login: POST returns 200 with skin overview page
        httpx_mock.add_response(
            method="POST",
            url=f"{ADYEN_TEST_BASE}{_LOGIN_ACTION}",
            status_code=200,
            html="<html><body>Skin Overview</body></html>",
        )
        client.login(credentials.account, credentials.user, credentials.password)
        assert client.authenticated is True

    def test_login_raises_on_wrong_credentials(
        self, client: AdyenAdminClient, httpx_mock: HTTPXMock,
    ) -> None:
        # Adyen stays on the login page on failure
        httpx_mock.add_response(
            method="POST",
            url=f"{ADYEN_TEST_BASE}{_LOGIN_ACTION}",
            status_code=200,
            html='<html><body><input name="j_username"/></body></html>',
        )
        with pytest.raises(AuthenticationError):
            client.login("AcmetoCasino", "fake", "wrong")

        assert client.authenticated is False

    def test_not_authenticated_by_default(self, client: AdyenAdminClient) -> None:
        assert client.authenticated is False

    def test_reset_session_clears_auth_flag(
        self, client: AdyenAdminClient, credentials: AdyenCredentials,
        httpx_mock: HTTPXMock,
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{ADYEN_TEST_BASE}{_LOGIN_ACTION}",
            status_code=200,
            html="<html>Overview</html>",
        )
        client.login(credentials.account, credentials.user, credentials.password)
        assert client.authenticated is True
        client.reset_session()
        assert client.authenticated is False


# ---------------------------------------------------------------------------
# AdyenAdminClient — session expiry detection
# ---------------------------------------------------------------------------


class TestAdyenAdminClientSessionExpiry:
    def test_get_raises_auth_error_when_not_logged_in(
        self, client: AdyenAdminClient, httpx_mock: HTTPXMock,
    ) -> None:
        """An unauthenticated GET that gets a login page → AuthenticationError."""
        httpx_mock.add_response(
            method="GET",
            url=f"{ADYEN_TEST_BASE}/ca/ca/skin/overview.shtml",
            status_code=200,
            html='<html><input name="j_username"/></html>',
        )
        with pytest.raises(AuthenticationError):
            client.get("/ca/ca/skin/overview.shtml")

    def test_auth_flag_set_to_false_on_session_expiry(
        self, client: AdyenAdminClient, httpx_mock: HTTPXMock,
    ) -> None:
        client._authenticated = True  # manually mark as authenticated

        httpx_mock.add_response(
            method="GET",
            url=f"{ADYEN_TEST_BASE}/ca/ca/skin/overview.shtml",
            status_code=200,
            html='<html><input name="j_username"/></html>',
        )
        with pytest.raises(AuthenticationError):
            client.get("/ca/ca/skin/overview.shtml")

        assert client.authenticated is False


# ---------------------------------------------------------------------------
# AdyenAdmin facade
# ---------------------------------------------------------------------------


class TestAdyenAdmin:
    def test_from_credentials(self) -> None:
        admin = AdyenAdmin.from_credentials("Acme", "user", "pass")
        assert admin._credentials.account == "Acme"
        assert admin.authenticated is False

    def test_from_credentials_file(self, tmp_path) -> None:
        yml = tmp_path / "credentials.yml"
        yml.write_text("account: FileAccount\nuser: fu\npassword: fp\n")
        admin = AdyenAdmin.from_credentials_file(yml)
        assert admin._credentials.account == "FileAccount"

    def test_context_manager(self) -> None:
        with AdyenAdmin.from_credentials("A", "u", "p") as admin:
            assert admin is not None
