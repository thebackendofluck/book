# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""SFTP upload/download wrapper with retry + OpsGenie alerting.

Python port of SftpUploader.scala referenced in chapter 37. Wraps an
arbitrary `SftpClient` Protocol so the business logic is testable
without a real SSH connection, and retries each operation once on
first failure before declaring it dead and alerting.

The retry semantics are deliberately limited to a single retry: the
file transfers that this module handles run as scheduled overnight
batch jobs, and a cascade of retries that takes longer than the job
window is strictly worse than a single-retry-then-alert because it
pushes the failure past the point where the on-call engineer can
intervene before the next marketing campaign fires.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from task import Alerter, NullAlerter  # type: ignore[import-not-found]

LOG = logging.getLogger("exacttarget_sync.sftp")


class SftpClient(Protocol):
    """Minimal SFTP client interface. Production uses paramiko; tests
    inject an in-memory stub that tracks calls.
    """

    def put(self, local_path: str, remote_path: str) -> None: ...

    def get(self, remote_path: str, local_path: str) -> None: ...

    def list_dir(self, remote_path: str) -> list[str]: ...


@dataclass
class TransferResult:
    """Outcome of a single upload or download attempt."""

    success: bool
    attempts: int
    remote_path: str
    local_path: str
    error_message: str | None = None


class SftpUploader:
    """High-level upload/download coordinator with retry and alerting."""

    def __init__(
        self,
        client: SftpClient,
        *,
        alerter: Alerter | None = None,
        retry_delay_seconds: float = 5.0,
    ) -> None:
        self._client = client
        self._alerter = alerter or NullAlerter()
        self._retry_delay = retry_delay_seconds

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload(self, local_path: str | Path, remote_path: str) -> TransferResult:
        """Upload a single file with one retry on first failure."""
        local = str(local_path)
        return self._with_retry(
            op_name="upload",
            op=lambda: self._client.put(local, remote_path),
            remote_path=remote_path,
            local_path=local,
        )

    def upload_all(
        self, files: list[tuple[Path, str]]
    ) -> list[TransferResult]:
        """Upload a list of (local_path, remote_path) pairs.

        All uploads are attempted even if earlier ones fail; the
        caller inspects the TransferResult list to decide whether to
        treat partial success as acceptable.
        """
        return [self.upload(local, remote) for local, remote in files]

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(self, remote_path: str, local_path: str | Path) -> TransferResult:
        """Download a single file with one retry on first failure."""
        local = str(local_path)
        return self._with_retry(
            op_name="download",
            op=lambda: self._client.get(remote_path, local),
            remote_path=remote_path,
            local_path=local,
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_dir(self, remote_path: str) -> list[str]:
        """List files in a remote directory. No retry -- listings are
        cheap and a failure is immediate operator feedback.
        """
        try:
            return self._client.list_dir(remote_path)
        except Exception as err:  # noqa: BLE001
            self._safely_alert(
                "list_dir",
                f"SFTP list_dir({remote_path}) failed: {err}",
            )
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _with_retry(
        self,
        *,
        op_name: str,
        op: "object",
        remote_path: str,
        local_path: str,
    ) -> TransferResult:
        # The `op` callable is wrapped here instead of using functools.partial
        # so that exceptions surface through the call site naturally.
        attempts = 0
        last_error: str | None = None

        for attempt in (1, 2):
            attempts = attempt
            try:
                op()  # type: ignore[operator]
                return TransferResult(
                    success=True,
                    attempts=attempts,
                    remote_path=remote_path,
                    local_path=local_path,
                )
            except Exception as err:  # noqa: BLE001
                last_error = f"attempt {attempt}: {err}"
                LOG.warning(
                    "sftp %s %s -> %s failed on attempt %d: %s",
                    op_name, local_path, remote_path, attempt, err,
                )
                if attempt == 1:
                    time.sleep(self._retry_delay)

        self._safely_alert(
            op_name,
            f"SFTP {op_name} {local_path} <-> {remote_path} failed after 2 attempts: {last_error}",
        )
        return TransferResult(
            success=False,
            attempts=attempts,
            remote_path=remote_path,
            local_path=local_path,
            error_message=last_error,
        )

    def _safely_alert(self, op: str, message: str) -> None:
        try:
            self._alerter.alert(f"sftp.{op}", message, priority="P2")
        except Exception as err:  # noqa: BLE001
            LOG.error("alerter raised on sftp %s: %s", op, err)
