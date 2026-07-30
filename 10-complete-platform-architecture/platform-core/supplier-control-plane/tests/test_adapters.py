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
tests/test_adapters.py
----------------------
Test suite for real adapter implementations in the Supplier Control Plane.

Covers:
  1. RSA-SHA256 signature validation (real cryptographic verification)
  2. WalletAdapter interface and implementations
  3. Domain handlers with WalletAdapter integration
  4. EnvVarSecretBackend
  5. VaultSecretBackend (via mock HTTP session)
  6. CallbackHandler with wallet adapter wiring
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

import pytest

from callback_handler import (
    CallbackHandler,
    CallbackStatus,
    CallbackType,
    DomainHandler,
    GameRoundHandler,
    SignatureMethod,
    SignatureValidator,
    WalletHandler,
)
from wallet_adapter import (
    HttpWalletAdapter,
    StubWalletAdapter,
    WalletAdapter,
)
from rsa_validator import validate_rsa_sha256
from secret_backends import EnvVarSecretBackend, VaultSecretBackend
from credential_manager import (
    CredentialManager,
    InMemorySecretBackend,
)
from models import (
    Credentials,
    SupplierCapabilityMatrix,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
    WalletModel,
)
from registry import SupplierRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockResponse:
    """Simulates an HTTP response."""

    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class MockSession:
    """Simulates an HTTP session with canned responses."""

    def __init__(self):
        self._routes: dict[str, MockResponse] = {}
        self._post_log: list[tuple] = []
        self._delete_log: list[str] = []

    def register(self, url, data, status=200):
        self._routes[url] = MockResponse(data, status)

    def get(self, url, headers=None):
        if url in self._routes:
            return self._routes[url]
        return MockResponse({}, status_code=404)

    def post(self, url, json=None, headers=None):
        self._post_log.append((url, json))
        return MockResponse({"data": json or {}})

    def delete(self, url, headers=None):
        self._delete_log.append(url)
        return MockResponse({}, status_code=204)


def _make_registry_and_creds():
    """Create a registry with one supplier and matching credentials."""
    reg = SupplierRegistry()
    record = SupplierRecord(
        id="evolution",
        name="Evolution Gaming",
        type=SupplierType.CASINO,
        status=SupplierStatus.ACTIVE,
        capabilities=SupplierCapabilityMatrix(
            supplier_id="evolution",
            games={"blackjack", "roulette"},
            currencies={"EUR"},
            jurisdictions={"GB"},
            wallet_model=WalletModel.SEAMLESS,
        ),
    )
    reg.register_supplier(record)

    backend = InMemorySecretBackend()
    cred_mgr = CredentialManager(registry=reg, backend=backend)
    creds = Credentials(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        api_key="TESTKEY123",
        api_secret="TESTSECRET456",
        operator_id="OP1",
    )
    cred_mgr.add_credentials(creds)

    return reg, cred_mgr


# ===========================================================================
# 1. RSA-SHA256 Signature Validation
# ===========================================================================


class TestRSASignatureValidation:
    """Tests for real RSA-SHA256 signature verification."""

    @pytest.fixture
    def rsa_keys(self):
        """Generate an RSA key pair for testing."""
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization

            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")

            public_pem = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")

            return private_key, private_pem, public_pem
        except ImportError:
            pytest.skip("cryptography library not installed")

    def test_rsa_sha256_valid_signature(self, rsa_keys):
        """Valid RSA-SHA256 signature should be accepted."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key, _, public_pem = rsa_keys
        body = '{"transaction_id": "TXN-RSA-001", "callback_type": "game_round"}'

        signature = private_key.sign(
            body.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        assert validate_rsa_sha256(body, public_pem, sig_b64) is True

    def test_rsa_sha256_invalid_signature(self, rsa_keys):
        """Tampered signature should be rejected."""
        _, _, public_pem = rsa_keys
        assert validate_rsa_sha256(
            "some body", public_pem, base64.b64encode(b"garbage").decode(),
        ) is False

    def test_rsa_sha256_wrong_body(self, rsa_keys):
        """Signature for a different body should be rejected."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key, _, public_pem = rsa_keys
        original = "original body"
        signature = private_key.sign(
            original.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        assert validate_rsa_sha256(
            "tampered body", public_pem, sig_b64,
        ) is False

    def test_rsa_dispatched_directly(self, rsa_keys):
        """RSA validation works through the standalone function."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key, _, public_pem = rsa_keys
        body = "test dispatch"

        signature = private_key.sign(
            body.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        assert validate_rsa_sha256(body, public_pem, sig_b64) is True

    def test_rsa_e2e_sign_and_verify(self, rsa_keys):
        """End-to-end: sign a payload and verify with validate_rsa_sha256."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key, _, public_pem = rsa_keys

        payload = {
            "transaction_id": "TXN-RSA-E2E",
            "callback_type": "game_round",
            "action": "bet",
        }
        body = json.dumps(payload, sort_keys=True)
        signature = private_key.sign(
            body.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")

        assert validate_rsa_sha256(body, public_pem, sig_b64) is True
        # Tampered body should fail
        assert validate_rsa_sha256(body + "x", public_pem, sig_b64) is False


# ===========================================================================
# 2. WalletAdapter interface and implementations
# ===========================================================================


class TestWalletAdapter:
    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            WalletAdapter()  # type: ignore[abstract]

    def test_stub_default_balance(self):
        adapter = StubWalletAdapter()
        assert adapter.get_balance("nonexistent") == 0.0

    def test_stub_set_and_get(self):
        adapter = StubWalletAdapter()
        adapter.set_balance("p1", 150.50)
        assert adapter.get_balance("p1") == 150.50

    def test_http_wallet_adapter(self):
        session = MockSession()
        session.register(
            "http://wallet:8000/players/p1/balance",
            {"balance": 250.75},
        )
        adapter = HttpWalletAdapter("http://wallet:8000", session=session)
        assert adapter.get_balance("p1") == 250.75


# ===========================================================================
# 3. Domain handlers with WalletAdapter integration
# ===========================================================================


class TestDomainHandlersWithWallet:
    def test_stub_wallet_adapter_returns_balance(self):
        """StubWalletAdapter returns set balances correctly."""
        wallet = StubWalletAdapter()
        wallet.set_balance("P-1", 100.50)
        assert wallet.get_balance("P-1") == 100.50
        assert wallet.get_balance("nonexistent") == 0.0

    def test_http_wallet_adapter_calls_api(self):
        """HttpWalletAdapter calls the REST API via session."""
        session = MockSession()
        session.register(
            "http://wallet:8000/players/P-2/balance",
            {"balance": 500.00},
        )
        adapter = HttpWalletAdapter("http://wallet:8000", session=session)
        assert adapter.get_balance("P-2") == 500.00

    def test_wallet_adapter_is_abstract(self):
        """Cannot instantiate WalletAdapter directly."""
        with pytest.raises(TypeError):
            WalletAdapter()  # type: ignore[abstract]


# ===========================================================================
# 4. EnvVarSecretBackend
# ===========================================================================


class TestEnvVarSecretBackend:
    def test_read_missing_returns_none(self):
        backend = EnvVarSecretBackend()
        assert backend.read("nonexistent/path") is None

    def test_write_and_read(self):
        backend = EnvVarSecretBackend()
        path = "test/write/read"
        value = {"api_key": "K1", "api_secret": "S1"}
        try:
            backend.write(path, value)
            result = backend.read(path)
            assert result == value
        finally:
            backend.delete(path)

    def test_delete(self):
        backend = EnvVarSecretBackend()
        path = "test/delete/me"
        backend.write(path, {"key": "val"})
        backend.delete(path)
        assert backend.read(path) is None

    def test_env_key_format(self):
        backend = EnvVarSecretBackend()
        key = backend._env_key("suppliers/evo/brands/b1/jurisdictions/GB")
        assert key == "SECRET_SUPPLIERS_EVO_BRANDS_B1_JURISDICTIONS_GB"

    def test_malformed_json_returns_none(self):
        backend = EnvVarSecretBackend()
        env_key = backend._env_key("bad/json/path")
        os.environ[env_key] = "not-valid-json"
        try:
            assert backend.read("bad/json/path") is None
        finally:
            os.environ.pop(env_key, None)


# ===========================================================================
# 5. VaultSecretBackend (via mock HTTP session)
# ===========================================================================


class TestVaultSecretBackend:
    def test_read_existing_secret(self):
        session = MockSession()
        session.register(
            "http://vault:8200/v1/secret/data/suppliers/evo/brands/b1/jurisdictions/GB",
            {"data": {"data": {"api_key": "VK1", "api_secret": "VS1"}}},
        )
        backend = VaultSecretBackend(
            vault_addr="http://vault:8200",
            vault_token="test-token",
            session=session,
        )
        result = backend.read("suppliers/evo/brands/b1/jurisdictions/GB")
        assert result == {"api_key": "VK1", "api_secret": "VS1"}

    def test_read_missing_returns_none(self):
        session = MockSession()
        # MockSession returns 404 for unknown URLs
        backend = VaultSecretBackend(
            vault_addr="http://vault:8200",
            vault_token="test-token",
            session=session,
        )
        result = backend.read("nonexistent/path")
        assert result is None

    def test_write_sends_post(self):
        session = MockSession()
        backend = VaultSecretBackend(
            vault_addr="http://vault:8200",
            vault_token="test-token",
            session=session,
        )
        backend.write("test/path", {"key": "val"})
        assert len(session._post_log) == 1
        url, data = session._post_log[0]
        assert "test/path" in url
        assert data == {"data": {"key": "val"}}

    def test_delete_sends_delete(self):
        session = MockSession()
        backend = VaultSecretBackend(
            vault_addr="http://vault:8200",
            vault_token="test-token",
            session=session,
        )
        backend.delete("test/path")
        assert len(session._delete_log) == 1
        assert "test/path" in session._delete_log[0]

    def test_credential_manager_with_vault_backend(self):
        """CredentialManager works with VaultSecretBackend."""
        session = MockSession()
        session.register(
            "http://vault:8200/v1/secret/data/suppliers/netent/brands/brand2/jurisdictions/SE",
            {"data": {"data": {
                "api_key": "NK1",
                "api_secret": "NS1",
                "operator_id": "OP-NE",
            }}},
        )

        backend = VaultSecretBackend(
            vault_addr="http://vault:8200",
            vault_token="test-token",
            session=session,
        )
        reg = SupplierRegistry()
        reg.register_supplier(SupplierRecord(
            id="netent",
            name="NetEnt",
            type=SupplierType.CASINO,
            status=SupplierStatus.ACTIVE,
        ))
        cred_mgr = CredentialManager(registry=reg, backend=backend)

        creds = cred_mgr.get_credentials("netent", "brand2", "SE")
        assert creds.api_key == "NK1"
        assert creds.api_secret == "NS1"
        assert creds.operator_id == "OP-NE"


# ===========================================================================
# 6. Integration: all pieces together
# ===========================================================================


class TestFullAdapterIntegration:
    def test_rsa_sign_verify_with_vault_creds(self):
        """
        End-to-end: RSA key pair, credentials stored in Vault backend,
        signature verified with validate_rsa_sha256.
        """
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
        except ImportError:
            pytest.skip("cryptography library not installed")

        # Generate keys
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048,
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        # Store public key in Vault backend (mock)
        session = MockSession()
        vault_path = "suppliers/supplier_x/brands/b1/jurisdictions/MT"
        session.register(
            f"http://vault:8200/v1/secret/data/{vault_path}",
            {"data": {"data": {
                "api_key": "SX-KEY",
                "api_secret": public_pem,
                "operator_id": "OP-SX",
            }}},
        )
        backend = VaultSecretBackend(
            vault_addr="http://vault:8200",
            vault_token="test-token",
            session=session,
        )
        # Read back credentials
        creds = backend.read(vault_path)
        assert creds is not None
        assert creds["api_key"] == "SX-KEY"

        # Sign a payload and verify
        payload = {
            "transaction_id": "TXN-FULL-001",
            "callback_type": "game_round",
            "action": "result",
            "player_id": "player-42",
        }
        body = json.dumps(payload, sort_keys=True)
        sig = private_key.sign(
            body.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        assert validate_rsa_sha256(
            body, creds["api_secret"], base64.b64encode(sig).decode(),
        ) is True
