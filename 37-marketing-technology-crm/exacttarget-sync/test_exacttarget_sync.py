# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Unit tests for the chapter-37 exacttarget-sync modules.

Covers the behaviours documented in chapter 37:
- Task lifecycle hooks fire even on failure, alerts fire once
- CSV exporter splits at the configured line count and produces
  one file per chunk
- SFTP uploader retries once then alerts on persistent failure
- MarketingPreferencesDao performs an idempotent upsert
- PlayersExportTask tokenises emails correctly
- UnsubBouncesImportTask parses CSVs with flexible column naming
- CLI outcome reports correct exit codes

Run with: python -m unittest test_exacttarget_sync
"""

from __future__ import annotations

import csv
import importlib.util
import io
import sys
import unittest
from datetime import datetime, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest import mock

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# Several chapters ship a top-level `config.py` (chapter-28 task-scheduler,
# chapter-30 onboarding, chapter-43 ai-governance, ...). Pre-install this
# chapter's copy via importlib so `from config import AlertingConfig`
# resolves to the exacttarget-sync variant regardless of pytest's global
# sys.modules state. Same story for `task`, `run` and the other
# short-named siblings.
SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str) -> None:
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


for _mod, _file in [
    ("config", "config.py"),
    ("task", "task.py"),
    ("csv_exporter", "csv_exporter.py"),
    ("sftp_uploader", "sftp_uploader.py"),
    ("marketing_preferences_dao", "marketing_preferences_dao.py"),
    ("players_export_task", "players_export_task.py"),
    ("unsub_bounces_import_task", "unsub_bounces_import_task.py"),
    ("run", "run.py"),
]:
    _load_local_module(_mod, _file)

from config import (  # noqa: E402
    AlertingConfig,
    BrandConfig,
    DatabaseConfig,
    ExportConfig,
    SyncConfig,
)
from csv_exporter import CsvExporter, cleanup_old_files  # noqa: E402
from marketing_preferences_dao import (  # noqa: E402
    MarketingPreferencesDao,
    MarketingReason,
)
from players_export_task import PlayerRecord, PlayersExportTask  # noqa: E402
from run import CliOutcome, main as run_main  # noqa: E402
from sftp_uploader import SftpUploader  # noqa: E402
from task import NullAlerter, Task, TaskResult  # noqa: E402
from unsub_bounces_import_task import UnsubBouncesImportTask  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(tmp: Path) -> SyncConfig:
    return SyncConfig(
        brands={
            "acme": BrandConfig(
                brand_id="acme",
                soap_endpoint="https://webservice.s7.exacttarget.com/",
                auth_endpoint="https://auth.exacttargetapis.com/v2/token",
                oauth_client_id="id",
                oauth_client_secret="secret",
                sftp_host="sftp.example",
                sftp_port=22,
                sftp_username="u",
                sftp_private_key_path="/tmp/key",
                sftp_upload_dir="/upload",
                sftp_download_dir="/download",
            ),
        },
        database=DatabaseConfig(
            host="db.example",
            port=5432,
            database="platform",
            username="u",
            password="p",
        ),
        export=ExportConfig(
            local_export_dir=str(tmp),
            csv_split_line_count=2,
            safety_overlap_hours=4,
            retention_days=3,
        ),
        alerting=AlertingConfig(opsgenie_api_key="k", opsgenie_team="t", enabled=True),
    )


class _StubSftp:
    """In-memory SFTP double."""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[tuple[str, str, str]] = []
        self.files: dict[str, bytes] = {}
        self._downloads_seed: dict[str, bytes] = {}
        self._attempt = 0

    def seed_download(self, remote_path: str, payload: bytes) -> None:
        self._downloads_seed[remote_path] = payload

    def put(self, local_path: str, remote_path: str) -> None:
        self._attempt += 1
        if self.fail_first and self._attempt == 1:
            raise RuntimeError("first upload fails")
        with open(local_path, "rb") as fh:
            self.files[remote_path] = fh.read()
        self.calls.append(("put", local_path, remote_path))

    def get(self, remote_path: str, local_path: str) -> None:
        data = self._downloads_seed.get(remote_path)
        if data is None:
            raise FileNotFoundError(remote_path)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(data)
        self.calls.append(("get", local_path, remote_path))

    def list_dir(self, remote_path: str) -> list[str]:
        return list(self.files)


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------


class _SuccessTask(Task):
    def __init__(self, alerter: NullAlerter) -> None:
        super().__init__(alerter=alerter)
        self.calls: list[str] = []

    def before(self) -> None:
        self.calls.append("before")

    def do_task(self) -> None:
        self.calls.append("do_task")

    def after(self) -> None:
        self.calls.append("after")


class _FailDoTask(Task):
    def __init__(self, alerter: NullAlerter) -> None:
        super().__init__(alerter=alerter)
        self.calls: list[str] = []

    def before(self) -> None:
        self.calls.append("before")

    def do_task(self) -> None:
        self.calls.append("do_task")
        raise RuntimeError("boom")

    def after(self) -> None:
        self.calls.append("after")


class TaskLifecycleTests(unittest.TestCase):
    def test_success_runs_all_hooks_in_order(self) -> None:
        alerter = NullAlerter()
        task = _SuccessTask(alerter)
        result = task.run()
        self.assertTrue(result.success)
        self.assertEqual(task.calls, ["before", "do_task", "after"])
        self.assertEqual(alerter.calls, [])

    def test_failure_in_do_task_still_runs_after_and_alerts(self) -> None:
        alerter = NullAlerter()
        task = _FailDoTask(alerter)
        result = task.run()
        self.assertFalse(result.success)
        self.assertEqual(task.calls, ["before", "do_task", "after"])
        self.assertEqual(len(alerter.calls), 1)
        self.assertIn("do_task() failed", alerter.calls[0][1])


# ---------------------------------------------------------------------------
# CSV exporter
# ---------------------------------------------------------------------------


class CsvExporterTests(unittest.TestCase):
    def test_splits_at_configured_line_count(self) -> None:
        with TemporaryDirectory() as tmp:
            exporter = CsvExporter(tmp, "players", ["UserId", "Email"], split_line_count=2)
            rows = [{"UserId": i, "Email": f"p{i}@x.com"} for i in range(5)]
            result = exporter.export(iter(rows))
            self.assertEqual(result.total_rows, 5)
            self.assertEqual(result.file_count, 3)  # 2 + 2 + 1
            # Each file has a header + data rows
            for path in result.files_written:
                content = path.read_text()
                self.assertTrue(content.startswith("UserId,Email"))

    def test_disables_splitting_when_split_is_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            exporter = CsvExporter(tmp, "players", ["UserId"], split_line_count=0)
            rows = [{"UserId": i} for i in range(10)]
            result = exporter.export(iter(rows))
            self.assertEqual(result.file_count, 1)
            self.assertEqual(result.total_rows, 10)

    def test_missing_header_field_raises_keyerror(self) -> None:
        with TemporaryDirectory() as tmp:
            exporter = CsvExporter(tmp, "p", ["A", "B"], split_line_count=0)
            with self.assertRaises(KeyError):
                exporter.export([{"A": 1}])

    def test_cleanup_old_files_respects_retention(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "old.csv"
            p.write_text("")
            # Backdate the mtime
            import os as _os
            old_time = datetime.now(tz=timezone.utc).timestamp() - (10 * 86_400)
            _os.utime(p, (old_time, old_time))
            deleted = cleanup_old_files(tmp, retention_days=3)
            self.assertEqual(deleted, 1)


# ---------------------------------------------------------------------------
# SFTP uploader
# ---------------------------------------------------------------------------


class SftpUploaderTests(unittest.TestCase):
    def test_success_on_first_attempt(self) -> None:
        stub = _StubSftp(fail_first=False)
        uploader = SftpUploader(stub)
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.csv"
            p.write_bytes(b"hello")
            result = uploader.upload(p, "/upload/f.csv")
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 1)

    def test_retry_on_first_failure_then_succeeds(self) -> None:
        stub = _StubSftp(fail_first=True)
        uploader = SftpUploader(stub, retry_delay_seconds=0.0)
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.csv"
            p.write_bytes(b"hi")
            result = uploader.upload(p, "/upload/f.csv")
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)

    def test_persistent_failure_alerts_once(self) -> None:
        class AlwaysFails:
            def put(self, local_path: str, remote_path: str) -> None:
                raise RuntimeError("nope")
            def get(self, remote_path: str, local_path: str) -> None: ...
            def list_dir(self, remote_path: str) -> list[str]: return []

        alerter = NullAlerter()
        uploader = SftpUploader(AlwaysFails(), alerter=alerter, retry_delay_seconds=0.0)
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.csv"
            p.write_bytes(b"hi")
            result = uploader.upload(p, "/upload/f.csv")
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(alerter.calls), 1)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class _StubCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._next_row: "tuple | None" = None

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params or ())))

    def queue_row(self, row):
        self._next_row = row

    def fetchone(self):
        row = self._next_row
        self._next_row = None
        return row

    def fetchall(self):
        return []

    def close(self):
        return


class MarketingPreferencesDaoTests(unittest.TestCase):
    def test_record_unsubscribe_uses_upsert(self) -> None:
        cursor = _StubCursor()
        dao = MarketingPreferencesDao(cursor_factory=lambda: cursor)
        dao.record_unsubscribe(42, source="exacttarget:acme")
        self.assertEqual(len(cursor.executed), 1)
        sql, params = cursor.executed[0]
        self.assertIn("INSERT INTO marketing_preferences", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertEqual(params[0], 42)
        self.assertFalse(params[1])  # email_enabled
        self.assertEqual(params[2], MarketingReason.UNSUBSCRIBE.value)

    def test_record_hard_bounce_sets_hard_bounce_reason(self) -> None:
        cursor = _StubCursor()
        dao = MarketingPreferencesDao(cursor_factory=lambda: cursor)
        dao.record_hard_bounce(42)
        self.assertEqual(cursor.executed[0][1][2], MarketingReason.HARD_BOUNCE.value)


# ---------------------------------------------------------------------------
# PlayersExportTask
# ---------------------------------------------------------------------------


class _StubRepo:
    def __init__(self, players: list[PlayerRecord]) -> None:
        self.players = players
        self.last_since: "datetime | None" = None

    def find_modified_since(self, since):
        self.last_since = since
        return self.players


class PlayersExportTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = _config(Path(self.tmp.name))
        self.repo = _StubRepo([
            PlayerRecord(
                user_id=i,
                email=f"player{i}@example.com",
                first_name="F",
                last_name="L",
                modified_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
            )
            for i in range(5)
        ])
        self.sftp_stub = _StubSftp()
        self.uploader = SftpUploader(self.sftp_stub)

    def test_tokenised_mode_anonymises_emails(self) -> None:
        task = PlayersExportTask(
            config=self.cfg,
            brand_id="acme",
            repository=self.repo,
            uploader=self.uploader,
            tokenized=True,
            full_range=True,
        )
        result = task.run()
        self.assertTrue(result.success)
        # Inspect what the SFTP stub received
        uploaded = list(self.sftp_stub.files.values())
        self.assertTrue(uploaded)
        content = uploaded[0].decode("utf-8")
        self.assertIn("0@example.com", content)
        self.assertNotIn("player0@example.com", content)

    def test_full_range_mode_queries_without_since(self) -> None:
        task = PlayersExportTask(
            config=self.cfg,
            brand_id="acme",
            repository=self.repo,
            uploader=self.uploader,
            full_range=True,
        )
        task.run()
        self.assertIsNone(self.repo.last_since)

    def test_split_count_produces_multiple_files(self) -> None:
        # cfg.export.csv_split_line_count = 2, 5 players -> 3 files
        task = PlayersExportTask(
            config=self.cfg,
            brand_id="acme",
            repository=self.repo,
            uploader=self.uploader,
            full_range=True,
        )
        task.run()
        self.assertEqual(len(self.sftp_stub.files), 3)


# ---------------------------------------------------------------------------
# UnsubBouncesImportTask
# ---------------------------------------------------------------------------


class UnsubBouncesImportTaskTests(unittest.TestCase):
    def test_import_applies_both_feeds(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = _config(Path(tmp.name))

        sftp = _StubSftp()
        unsub_csv = b"SubscriberKey,EmailAddress\n11,p11@x.com\n12,p12@x.com\n"
        bounce_csv = b"SubscriberKey,EmailAddress\n13,p13@x.com\n"
        today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        sftp.seed_download(f"/download/unsubs_{today}.csv", unsub_csv)
        sftp.seed_download(f"/download/bounces_{today}.csv", bounce_csv)

        recorded: list[tuple[str, int]] = []

        class _FakeDao:
            def record_unsubscribe(self, user_id: int, *, source: str = "") -> None:
                recorded.append(("unsub", user_id))
            def record_hard_bounce(self, user_id: int, *, source: str = "") -> None:
                recorded.append(("bounce", user_id))

        uploader = SftpUploader(sftp)
        task = UnsubBouncesImportTask(
            config=cfg,
            brand_id="acme",
            dao=_FakeDao(),  # type: ignore[arg-type]
            uploader=uploader,
        )
        result = task.run()
        self.assertTrue(result.success, msg=result.error_message)
        self.assertEqual(recorded, [
            ("unsub", 11), ("unsub", 12), ("bounce", 13),
        ])
        summary = task.summary
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.unsubs_applied, 2)
        self.assertEqual(summary.bounces_applied, 1)


# ---------------------------------------------------------------------------
# Run (CLI)
# ---------------------------------------------------------------------------


class RunCliTests(unittest.TestCase):
    def test_main_reports_zero_exit_when_tasks_succeed(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = _config(Path(tmp.name))

        def factory(c, brand, args):
            return _SuccessTask(NullAlerter())

        outcome = run_main(
            ["--brand", "acme", "--tasks", "playersExport"],
            config=cfg,
            task_factories={"playersExport": factory},
        )
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(len(outcome.successes), 1)

    def test_main_reports_nonzero_exit_when_any_task_fails(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = _config(Path(tmp.name))

        def good(c, brand, args):
            return _SuccessTask(NullAlerter())

        def bad(c, brand, args):
            return _FailDoTask(NullAlerter())

        outcome = run_main(
            ["--brand", "acme", "--tasks", "playersExport,unsubBouncesImport"],
            config=cfg,
            task_factories={"playersExport": good, "unsubBouncesImport": bad},
        )
        self.assertEqual(outcome.exit_code, 1)
        self.assertEqual(len(outcome.successes), 1)
        self.assertEqual(len(outcome.failures), 1)

    def test_unknown_task_name_exits_with_error(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = _config(Path(tmp.name))
        with self.assertRaises(SystemExit):
            run_main(
                ["--brand", "acme", "--tasks", "unknownTask"],
                config=cfg,
                task_factories={},
            )


if __name__ == "__main__":
    unittest.main()
