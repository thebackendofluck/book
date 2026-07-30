# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Unit tests for YubiHSM integration.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestYubiHSMConnector:
    """Test YubiHSM connector functionality."""

    def test_connector_initialization(self, mock_yubihsm, sample_hsm_config):
        """Test YubiHSM connector initialization."""
        from yubihsm import YubiHsm

        # Test successful initialization
        hsm = YubiHsm(**sample_hsm_config)
        assert hsm is not None
        mock_yubihsm.constructor_mock.assert_called_once_with(**sample_hsm_config)

    def test_key_generation(self, mock_yubihsm):
        """Test key generation in HSM."""
        mock_yubihsm.generate_key.return_value = "generated_key_123"

        # Mock key generation
        result = mock_yubihsm.generate_key(
            key_type="aes256",
            label="test-key",
            domains=1,
            capabilities=0xFF
        )

        assert result == "generated_key_123"
        mock_yubihsm.generate_key.assert_called_once_with(
            key_type="aes256",
            label="test-key",
            domains=1,
            capabilities=0xFF
        )

    def test_key_retrieval(self, mock_yubihsm, sample_key_id):
        """Test key retrieval from HSM."""
        mock_yubihsm.get_key.return_value = b"retrieved_key_data"

        result = mock_yubihsm.get_key(key_id=sample_key_id)

        assert result == b"retrieved_key_data"
        mock_yubihsm.get_key.assert_called_once_with(key_id=sample_key_id)

    def test_encryption_operation(self, mock_yubihsm, sample_encryption_key):
        """Test encryption operation."""
        plaintext = b"Hello, World!"
        ciphertext = b"encrypted_data"

        mock_yubihsm.encrypt.return_value = ciphertext

        result = mock_yubihsm.encrypt(
            key_id=1,
            algorithm="aes256-ccm",
            data=plaintext
        )

        assert result == ciphertext
        mock_yubihsm.encrypt.assert_called_once_with(
            key_id=1,
            algorithm="aes256-ccm",
            data=plaintext
        )

    def test_decryption_operation(self, mock_yubihsm):
        """Test decryption operation."""
        ciphertext = b"encrypted_data"
        plaintext = b"Hello, World!"

        mock_yubihsm.decrypt.return_value = plaintext

        result = mock_yubihsm.decrypt(
            key_id=1,
            algorithm="aes256-ccm",
            data=ciphertext
        )

        assert result == plaintext
        mock_yubihsm.decrypt.assert_called_once_with(
            key_id=1,
            algorithm="aes256-ccm",
            data=ciphertext
        )

    def test_audit_logging(self, mock_yubihsm):
        """Test audit log retrieval."""
        mock_logs = [
            {"timestamp": "2024-01-01T00:00:00Z", "operation": "key_gen", "status": "success"},
            {"timestamp": "2024-01-01T00:01:00Z", "operation": "encrypt", "status": "success"}
        ]
        mock_yubihsm.get_audit_logs.return_value = mock_logs

        result = mock_yubihsm.get_audit_logs()

        assert result == mock_logs
        mock_yubihsm.get_audit_logs.assert_called_once()

    def test_fips_mode_verification(self, mock_yubihsm):
        """Test FIPS mode verification."""
        mock_yubihsm.get_device_info.return_value = {"fips_mode": True}

        result = mock_yubihsm.get_device_info()

        assert result["fips_mode"] is True
        mock_yubihsm.get_device_info.assert_called_once()

    @pytest.mark.parametrize("operation", ["generate_key", "encrypt", "decrypt", "get_key"])
    def test_operation_error_handling(self, mock_yubihsm, operation):
        """Test error handling for various operations."""
        mock_yubihsm.configure_mock(**{operation: MagicMock(side_effect=Exception("HSM Error"))})

        with pytest.raises(Exception, match="HSM Error"):
            getattr(mock_yubihsm, operation)()

    def test_session_management(self, mock_yubihsm):
        """Test HSM session management."""
        # Test session open
        mock_yubihsm.open_session.return_value = "session_123"

        session = mock_yubihsm.open_session()
        assert session == "session_123"

        # Test session close
        mock_yubihsm.close_session.return_value = True
        result = mock_yubihsm.close_session(session)
        assert result is True

    def test_key_deletion(self, mock_yubihsm, sample_key_id):
        """Test key deletion."""
        mock_yubihsm.delete_key.return_value = True

        result = mock_yubihsm.delete_key(key_id=sample_key_id)

        assert result is True
        mock_yubihsm.delete_key.assert_called_once_with(key_id=sample_key_id)
