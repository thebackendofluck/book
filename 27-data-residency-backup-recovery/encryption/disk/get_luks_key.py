#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
get_luks_key.py - Retrieve LUKS encryption key from HSM
Used by cryptsetup to unlock LUKS-encrypted volumes at boot time.

Usage:
    get_luks_key.py <connector_url> <auth_key_id> <password> <key_id>

Integration:
    Called by systemd unlock services or crypttab keyscript entries
    to retrieve disk encryption keys from hardware security modules.
"""

import sys
from yubihsm import YubiHsm  # ty:ignore[unresolved-import]
from yubihsm.objects import Opaque  # ty:ignore[unresolved-import]


def get_luks_key(connector_url: str, auth_key_id: int,
                 password: str, key_id: int) -> str:
    """
    Retrieve LUKS key from HSM.

    Args:
        connector_url: HSM connector URL
        auth_key_id: Authentication key ID
        password: Authentication password
        key_id: LUKS key object ID in the HSM

    Returns:
        Hex-encoded key material
    """
    try:
        hsm = YubiHsm.connect(connector_url)
        session = hsm.create_session_derived(auth_key_id, password)

        # Retrieve opaque object containing LUKS key material
        opaque = Opaque(session, key_id)
        key_data = opaque.get()

        session.close()
        hsm.close()

        return key_data.hex()

    except Exception as e:
        print(f"Error retrieving LUKS key: {e}", file=sys.stderr)
        return None  # ty:ignore[invalid-return-type]


def main():
    """Main entry point for cryptsetup integration"""
    if len(sys.argv) != 5:
        print("Usage: get_luks_key.py <connector_url> <auth_key_id> "
              "<password> <key_id>", file=sys.stderr)
        sys.exit(1)

    connector_url = sys.argv[1]
    auth_key_id = int(sys.argv[2])
    password = sys.argv[3]
    key_id = int(sys.argv[4])

    key = get_luks_key(connector_url, auth_key_id, password, key_id)
    if key:
        print(key)
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
