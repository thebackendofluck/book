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
Domain models for the email suppression sync tool.

This tool synchronises the email suppression list between two platforms:
  SilverPop (Acoustic) -> ExactTarget (Salesforce Marketing Cloud)

Flow:
  1. For each configured SilverPop account, export the Master Suppression List
     for a given date range via the SilverPop XML API
  2. Download the exported CSV file via FTP
  3. Transform the CSV into ExactTarget format
  4. Upload the result to ExactTarget's FTP import directory

The SilverPop API is a SOAP-like XML API where you POST XML <Envelope> requests
and receive XML responses. Export is asynchronous: you submit a job and poll
until it reports COMPLETE status.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SPSettings(BaseModel):
    """SilverPop API and FTP credentials for a single account."""
    id: int
    api_url: str
    api_username: str
    api_password: str
    ftp_url: str
    ftp_username: str
    ftp_password: str


class SPSuppressionListItem(BaseModel):
    """A single suppression list entry exported from SilverPop."""
    email: str
    opt_out_date: str = ""
    opt_out_source: str = ""
    reason: str = ""


class AppConfig(BaseModel):
    sp_csv_directory: str = "/tmp/sp_csv"
    et_csv_directory: str = "/tmp/et_csv"
    et_ftp_host: str
    et_ftp_username: str
    et_ftp_password: str
    et_ftp_import_dir: str = "/import"


# Column mapping: SilverPop CSV field -> ExactTarget CSV column name
ET_COLUMN_MAPPING: dict[str, str] = {
    "email": "Email Address",
    "opt_out_date": "Opt Out Date",
    "opt_out_source": "Opt Out Source",
    "reason": "Reason",
}

ET_FIELD_NAMES: list[str] = list(ET_COLUMN_MAPPING.keys())
