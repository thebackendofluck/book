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
Pytest configuration and fixtures for YubiHSM 2 FIPS Enterprise Security Infrastructure tests.
"""

from pathlib import Path
import sys
import types

import pytest
import boto3
from unittest.mock import MagicMock, patch
from moto import mock_aws

CHAPTER_DIR = Path(__file__).resolve().parents[1]
TERRAFORM_DIR = CHAPTER_DIR / "terraform"


@pytest.fixture(scope="session")
def aws_credentials():
    """Mock AWS credentials for testing."""
    with patch.dict("os.environ", {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }):
        yield


@pytest.fixture(scope="function")
def aws_mock(aws_credentials):
    """Mock AWS services for testing."""
    with mock_aws():
        yield


@pytest.fixture
def mock_yubihsm():
    """Mock YubiHSM connector for testing."""
    yubihsm_module = types.ModuleType("yubihsm")
    yubihsm_core_module = types.ModuleType("yubihsm.core")
    mock_hsm = MagicMock(name="YubiHsm")
    yubihsm_module.YubiHsm = mock_hsm
    yubihsm_core_module.YubiHsm = mock_hsm
    with patch.dict(
        sys.modules,
        {
            "yubihsm": yubihsm_module,
            "yubihsm.core": yubihsm_core_module,
        },
    ):
        mock_instance = MagicMock()
        mock_hsm.return_value = mock_instance
        mock_instance.constructor_mock = mock_hsm
        yield mock_instance


@pytest.fixture
def mock_vaultwarden():
    """Mock Vaultwarden client for testing."""
    with patch("requests.Session") as mock_session:
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_session.return_value.get.return_value = mock_response
        mock_session.return_value.post.return_value = mock_response
        yield mock_session.return_value


@pytest.fixture
def sample_encryption_key():
    """Sample encryption key for testing."""
    return "0123456789abcdef" * 2  # 32 bytes


@pytest.fixture
def sample_key_id():
    """Sample key ID for testing."""
    return "test-key-001"


@pytest.fixture
def sample_hsm_config():
    """Sample HSM configuration for testing."""
    return {
        "connector_url": "http://localhost:12345",
        "auth_key_id": 1,
        "password": "test_password",
        "fips_mode": True
    }


@pytest.fixture
def sample_aws_config():
    """Sample AWS configuration for testing."""
    return {
        "region": "us-east-1",
        "kms_key_id": "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012",
        "vpc_id": "vpc-12345678",
        "subnet_ids": ["subnet-12345678", "subnet-87654321"]
    }


@pytest.fixture(autouse=True)
def mock_environment():
    """Mock environment variables for consistent testing."""
    env_vars = {
        "YUBIHSM_CONNECTOR_URL": "http://localhost:12345",
        "YUBIHSM_AUTH_KEY_ID": "1",
        "YUBIHSM_PASSWORD": "test_password",
        "VAULTWARDEN_URL": "https://vault.example.com",
        "VAULTWARDEN_API_KEY": "test_api_key",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing"
    }

    with patch.dict("os.environ", env_vars):
        yield
