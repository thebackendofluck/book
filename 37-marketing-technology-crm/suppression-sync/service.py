# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Suppression sync services.

Provides:
- SilverPopService: SilverPop XML API client (export suppression list asynchronously)
- FtpClient: upload/download files via FTP
- CsvExporter: write typed objects to CSV
- SPSuppressionCSVReader: read SilverPop export CSV
- SPSettingsDAO: load SilverPop account settings from DB
- SuppressionSyncProcessor: main orchestration

SilverPop API pattern:
  1. Login to get a session ID
  2. POST XML requests with the session ID in the URL (jsessionid)
  3. Export is asynchronous: submit ExportList request, poll GetJobStatus
  4. When COMPLETE, download the exported file path via FTP
"""

from __future__ import annotations

import csv
import ftplib
import io
import os
import time
from datetime import datetime
from typing import Iterator

import requests
import structlog
from lxml import etree
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .models import AppConfig, SPSettings, SPSuppressionListItem

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/platform")


# ---------------------------------------------------------------------------
# SilverPop XML API client
# ---------------------------------------------------------------------------

class SilverPopService:
    """
    Client for the SilverPop (Acoustic) XML API.

    Authentication: POST to the API URL without a session, extract SESSIONID
    from the response, then include it as a jsessionid query param on all
    subsequent requests.

    Export flow is asynchronous:
      1. POST GetLists to find the Master Suppression List ID
      2. POST ExportList with the list ID and date range
      3. Poll GetJobStatus until JOB_STATUS == "COMPLETE"
      4. Use the FILE_PATH from the ExportList response
    """

    JOB_POLL_DELAY_SECONDS = 10
    JOB_MAX_RETRIES = 50
    DATE_FORMAT = "%m/%d/%Y %H:%M:%S"

    def __init__(self, service_url: str, username: str, password: str) -> None:
        self._url = service_url
        self._username = username
        self._password = password
        self._session = requests.Session()

    def export_master_suppression_list(
        self, date_start: datetime, date_end: datetime
    ) -> str:
        """
        Export the Master Suppression List for the given date range.
        Returns the FTP file path of the exported CSV.
        """
        session_id = self._login()
        url_with_session = f"{self._url};jsessionid={session_id}"

        # Step 1: find the Master Suppression List ID
        lists_response = self._post_xml(url_with_session, self._get_lists_request())
        master_list_id = self._find_master_list_id(lists_response)

        # Step 2: submit export job
        export_response = self._post_xml(
            url_with_session,
            self._export_list_request(master_list_id, date_start, date_end),
        )
        file_path_elem = export_response.find(".//FILE_PATH")
        job_id_elem = export_response.find(".//JOB_ID")
        if file_path_elem is None or job_id_elem is None:
            raise RuntimeError("Missing FILE_PATH or JOB_ID in export response")
        file_path = file_path_elem.text or ""
        job_id = job_id_elem.text or ""

        # Step 3: poll for completion
        self._wait_for_job(url_with_session, job_id)
        return file_path

    def _wait_for_job(self, url: str, job_id: str) -> None:
        for attempt in range(self.JOB_MAX_RETRIES):
            log.debug(
                "silverpop.polling_job",
                job_id=job_id,
                attempt=attempt,
                max=self.JOB_MAX_RETRIES,
            )
            time.sleep(self.JOB_POLL_DELAY_SECONDS)
            status_resp = self._post_xml(url, self._job_status_request(job_id))
            status_elem = status_resp.find(".//JOB_STATUS")
            status = (status_elem.text or "").upper() if status_elem is not None else ""
            if status == "COMPLETE":
                return
            if status in ("ERROR", "CANCELED"):
                raise RuntimeError(f"Job {job_id} ended with status: {status}")
        raise RuntimeError(f"Job {job_id} did not complete within {self.JOB_MAX_RETRIES} retries")

    def _login(self) -> str:
        response = self._post_xml(self._url, self._login_request())
        session_elem = response.find(".//SESSIONID")
        if session_elem is None or not session_elem.text:
            raise RuntimeError("Failed to get session ID from SilverPop")
        return session_elem.text

    def _post_xml(self, url: str, body_xml: bytes) -> etree._Element:
        envelope = b"<Envelope><Body>" + body_xml + b"</Body></Envelope>"
        response = self._session.post(
            url,
            data=envelope,
            headers={"Content-Type": "text/xml; charset=UTF-8"},
            timeout=60,
        )
        response.raise_for_status()
        root = etree.fromstring(response.content)
        success_elem = root.find(".//SUCCESS")
        if success_elem is None or (success_elem.text or "").upper() not in ("TRUE", "true"):
            fault = root.find(".//FaultString")
            error_id = root.find(".//errorid")
            msg = f"SilverPop API error[{error_id.text if error_id is not None else '?'}]: {fault.text if fault is not None else 'Unknown'}"
            raise RuntimeError(msg)
        return root

    def _login_request(self) -> bytes:
        return (
            f"<Login><USERNAME>{self._username}</USERNAME>"
            f"<PASSWORD>{self._password}</PASSWORD></Login>"
        ).encode()

    def _get_lists_request(self) -> bytes:
        return b"<GetLists><VISIBILITY>0</VISIBILITY><LIST_TYPE>13</LIST_TYPE></GetLists>"

    def _export_list_request(
        self, list_id: str, date_start: datetime, date_end: datetime
    ) -> bytes:
        start = date_start.strftime(self.DATE_FORMAT)
        end = date_end.strftime(self.DATE_FORMAT)
        return (
            f"<ExportList>"
            f"<LIST_ID>{list_id}</LIST_ID>"
            f"<EXPORT_TYPE>ALL</EXPORT_TYPE>"
            f"<EXPORT_FORMAT>CSV</EXPORT_FORMAT>"
            f"<ADD_TO_STORED_FILES/>"
            f"<DATE_START>{start}</DATE_START>"
            f"<DATE_END>{end}</DATE_END>"
            f"</ExportList>"
        ).encode()

    def _job_status_request(self, job_id: str) -> bytes:
        return f"<GetJobStatus><JOB_ID>{job_id}</JOB_ID></GetJobStatus>".encode()

    def _find_master_list_id(self, response: etree._Element) -> str:
        for list_elem in response.findall(".//LIST"):
            name_elem = list_elem.find("NAME")
            id_elem = list_elem.find("ID")
            if name_elem is not None and (name_elem.text or "").upper() == "MASTER SUPPRESSION LIST":
                if id_elem is not None and id_elem.text:
                    return id_elem.text
        raise RuntimeError("Master Suppression List not found in GetLists response")


# ---------------------------------------------------------------------------
# FTP client
# ---------------------------------------------------------------------------

class FtpClient:
    """Upload/download files via FTP."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self._host = host
        self._username = username
        self._password = password

    def upload_file(self, local_path: str, remote_path: str) -> None:
        log.debug("ftp.uploading", local=local_path, remote=remote_path)
        with ftplib.FTP(self._host) as ftp:
            ftp.login(self._username, self._password)
            ftp.set_pasv(True)
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_path}", f)
        log.debug("ftp.uploaded", remote=remote_path)

    def download_file(self, remote_path: str, local_path: str) -> None:
        log.debug("ftp.downloading", remote=remote_path, local=local_path)
        with ftplib.FTP(self._host) as ftp:
            ftp.login(self._username, self._password)
            ftp.set_pasv(True)
            with open(local_path, "wb") as f:
                ftp.retrbinary(f"RETR {remote_path}", f.write)
        log.debug("ftp.downloaded", local=local_path)


# ---------------------------------------------------------------------------
# CSV utilities
# ---------------------------------------------------------------------------

class CsvExporter:
    """Write typed objects to a CSV file."""

    def __init__(
        self,
        path: str,
        fields: list[str],
        to_row: object,
        include_header: bool = True,
    ) -> None:
        self._path = path
        self._fields = fields
        self._to_row = to_row
        self._count = 0
        self._file = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=fields)
        if include_header:
            self._writer.writeheader()

    def add(self, obj: object) -> None:
        row = self._to_row(obj)  # type: ignore[operator]
        self._writer.writerow({f: row.get(f, "") for f in self._fields})
        self._count += 1

    def close(self) -> None:
        self._file.close()

    @property
    def count(self) -> int:
        return self._count


class SPSuppressionCSVReader:
    """Read a SilverPop suppression export CSV."""

    def __init__(self, path: str) -> None:
        self._path = path

    def __iter__(self) -> Iterator[SPSuppressionListItem]:
        with open(self._path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield SPSuppressionListItem(
                    email=row.get("Email Address", row.get("email", "")),
                    opt_out_date=row.get("Opt Out Date", ""),
                    opt_out_source=row.get("Opt Out Source", ""),
                    reason=row.get("Reason", ""),
                )


# ---------------------------------------------------------------------------
# Settings DAO
# ---------------------------------------------------------------------------

class SPSettingsDAO:
    """Load SilverPop account settings from the database."""

    SQL = """
        SELECT id, api_url, api_username, api_password,
               ftp_url, ftp_username, ftp_password
        FROM et.sp_settings
        WHERE id = ANY(:ids)
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_by_ids(self, ids: list[int]) -> list[SPSettings]:
        with self._engine.connect() as conn:
            rows = conn.execute(text(self.SQL), {"ids": ids}).mappings()
            return [
                SPSettings(
                    id=r["id"],
                    api_url=r["api_url"],
                    api_username=r["api_username"],
                    api_password=r["api_password"],
                    ftp_url=r["ftp_url"],
                    ftp_username=r["ftp_username"],
                    ftp_password=r["ftp_password"],
                )
                for r in rows
            ]


# ---------------------------------------------------------------------------
# Main processor
# ---------------------------------------------------------------------------

class SuppressionSyncProcessor:
    """
    Orchestrates the suppression list sync:
      SilverPop -> download via FTP -> transform to ET format -> upload to ET FTP
    """

    def __init__(
        self,
        settings_dao: SPSettingsDAO,
        app_config: AppConfig,
    ) -> None:
        self._dao = settings_dao
        self._config = app_config

    def run(
        self,
        sp_settings_ids: list[int],
        date_start: datetime,
        date_end: datetime,
    ) -> None:
        config = self._config
        os.makedirs(config.sp_csv_directory, exist_ok=True)
        os.makedirs(config.et_csv_directory, exist_ok=True)

        sp_settings_list = self._dao.list_by_ids(sp_settings_ids)

        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        et_csv_path = os.path.join(config.et_csv_directory, f"{ts}.csv")

        from .models import ET_FIELD_NAMES

        def sp_to_et_row(item: SPSuppressionListItem) -> dict[str, str]:
            return {
                "email": item.email,
                "opt_out_date": item.opt_out_date,
                "opt_out_source": item.opt_out_source,
                "reason": item.reason,
            }

        et_exporter = CsvExporter(et_csv_path, ET_FIELD_NAMES, sp_to_et_row, include_header=True)

        for sps in sp_settings_list:
            log.info("suppression_sync.exporting", api_username=sps.api_username)
            sp_api = SilverPopService(sps.api_url, sps.api_username, sps.api_password)
            remote_file_path = sp_api.export_master_suppression_list(date_start, date_end)
            log.debug("suppression_sync.remote_file", path=remote_file_path)

            sp_csv_path = os.path.join(
                config.sp_csv_directory,
                f"{sps.api_username}_{ts}.csv",
            )
            sp_ftp = FtpClient(sps.ftp_url, sps.ftp_username, sps.ftp_password)
            sp_ftp.download_file(remote_file_path, sp_csv_path)

            reader = SPSuppressionCSVReader(sp_csv_path)
            for item in reader:
                et_exporter.add(item)

        et_exporter.close()
        log.info("suppression_sync.et_csv_ready", path=et_csv_path, count=et_exporter.count)

        remote_et_name = (
            f"{config.et_ftp_import_dir}/spsuppression{datetime.now().strftime('%Y%m%d')}.csv"
        )
        et_ftp = FtpClient(config.et_ftp_host, config.et_ftp_username, config.et_ftp_password)
        et_ftp.upload_file(et_csv_path, remote_et_name)
        log.info("suppression_sync.uploaded", remote=remote_et_name)
