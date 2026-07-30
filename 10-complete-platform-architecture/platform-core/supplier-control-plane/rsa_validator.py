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
rsa_validator.py
----------------
RSA-SHA256 signature validation for supplier callbacks.

Uses the ``cryptography`` library for real RSA-PKCS1v15 verification.
"""

from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)


def validate_rsa_sha256(
    body: str,
    public_key_pem: str,
    provided_signature_b64: str,
) -> bool:
    """
    Validate RSA-SHA256 (PKCS#1 v1.5) signature.

    Parameters
    ----------
    body:                   The raw request body that was signed.
    public_key_pem:         PEM-encoded RSA public key from credential store.
    provided_signature_b64: Base64-encoded signature from X-Signature header.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        sig_bytes = base64.b64decode(provided_signature_b64)
        public_key = serialization.load_pem_public_key(
            public_key_pem.encode("utf-8"),
        )
        public_key.verify(
            sig_bytes,
            body.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except ImportError:
        logger.error(
            "cryptography library not installed; "
            "RSA-SHA256 validation unavailable"
        )
        return False
    except Exception:
        return False
