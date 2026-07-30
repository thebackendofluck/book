# Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# casino/mtls.py
"""mTLS client/server utilities for Python services."""

import asyncio
import logging
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class CertificateReloader(FileSystemEventHandler):
    """Watches certificate files and rebuilds SSL contexts on change."""

    def __init__(
        self,
        cert_file: str,
        key_file: str,
        ca_bundle: str,
        allowed_callers: Optional[set[str]] = None,
    ):
        self._cert_file = cert_file
        self._key_file = key_file
        self._ca_bundle = ca_bundle
        # Callers permitted to connect to this service's server context,
        # matched against the client cert's SPIFFE URI SAN or CN. Verifying
        # the chain against ca_bundle proves the cert is trusted by the
        # shared CA; it does not prove this specific caller is authorized.
        # Empty by default so a server that forgets to pass an allowlist
        # fails closed instead of accepting any trusted caller.
        self._allowed_callers = frozenset(allowed_callers or ())
        self._lock = threading.Lock()
        self._client_ctx: Optional[ssl.SSLContext] = None
        self._server_ctx: Optional[ssl.SSLContext] = None
        self._reload()

    def _reload(self) -> None:
        try:
            # Server context: requires client certs
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            server_ctx.load_cert_chain(self._cert_file, self._key_file)
            server_ctx.load_verify_locations(self._ca_bundle)
            server_ctx.verify_mode = ssl.CERT_REQUIRED

            # Client context: presents client cert, verifies server cert
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            client_ctx.load_cert_chain(self._cert_file, self._key_file)
            client_ctx.load_verify_locations(self._ca_bundle)
            client_ctx.verify_mode = ssl.CERT_REQUIRED
            client_ctx.check_hostname = True

            with self._lock:
                self._server_ctx = server_ctx
                self._client_ctx = client_ctx

            logger.info("TLS certificates reloaded from %s", self._cert_file)

        except Exception as e:
            logger.error("Failed to reload TLS certificates: %s", e)

    def on_modified(self, event):
        if event.src_path in (self._cert_file, self._key_file, self._ca_bundle):
            time.sleep(0.1)  # Wait for atomic write to complete
            self._reload()

    def on_created(self, event):
        self.on_modified(event)

    @property
    def server_context(self) -> ssl.SSLContext:
        with self._lock:
            assert self._server_ctx is not None, "server_context accessed before initialization"
            return self._server_ctx

    @property
    def client_context(self) -> ssl.SSLContext:
        with self._lock:
            assert self._client_ctx is not None, "client_context accessed before initialization"
            return self._client_ctx

    def require_allowed_caller(self, peer_cert: dict) -> None:
        """Fail closed unless the connecting peer's certificate identity is
        allowlisted. The Python ssl module has no equivalent of Go's
        VerifyPeerCertificate hook, so a server built on server_context must
        call this explicitly, right after accepting the TLS connection and
        before handling any request, with the dict returned by
        SSLSocket.getpeercert() (or ssl_object.getpeercert() from asyncio's
        transport extra info) for that connection.
        """
        identity = caller_identity(peer_cert)
        if identity is None or identity not in self._allowed_callers:
            raise ssl.SSLError(f"caller {identity!r} is not in the allowed caller list")


def caller_identity(peer_cert: dict) -> Optional[str]:
    """Extract the caller's SPIFFE URI SAN (preferred) or Common Name from a
    peer certificate dict as returned by ssl.SSLSocket.getpeercert()."""
    for typ, value in peer_cert.get("subjectAltName", ()):
        if typ == "URI":
            return value
    for rdn in peer_cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return None


def build_mtls_client(reloader: CertificateReloader, base_url: str) -> httpx.AsyncClient:
    """Create an httpx AsyncClient with mTLS configured."""
    return httpx.AsyncClient(
        base_url=base_url,
        verify=reloader.client_context,
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        http2=False,  # Stick to HTTP/1.1; HTTP/2 adds complexity for internal mTLS
    )


# Usage example in compliance-reporter service
async def report_transaction(
    client: httpx.AsyncClient,
    transaction: dict,
) -> dict:
    """Send a transaction record to the compliance API."""
    response = await client.post(
        "/v1/transactions",
        json=transaction,
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()
    return response.json()
