# Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# casino/compliance_reporter/main.py
import asyncio
import logging
import os
from pathlib import Path

from watchdog.observers import Observer
from mtls_cert_reloader import CertificateReloader, build_mtls_client, report_transaction  # type: ignore[import-untyped]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    cert_file  = os.environ["TLS_CERT_FILE"]
    key_file   = os.environ["TLS_KEY_FILE"]
    ca_bundle  = os.environ["TLS_CA_BUNDLE"]
    wallet_url = os.environ.get("WALLET_SERVICE_URL", "https://wallet-service.payments.svc.cluster.local:8443")

    reloader = CertificateReloader(cert_file, key_file, ca_bundle)

    observer = Observer()
    observer.schedule(reloader, path=str(Path(cert_file).parent), recursive=False)
    observer.start()

    async with build_mtls_client(reloader, wallet_url) as client:
        logger.info("compliance-reporter started, mTLS client ready")
        # Event loop for processing compliance transactions
        # ... service implementation


if __name__ == "__main__":
    asyncio.run(main())
