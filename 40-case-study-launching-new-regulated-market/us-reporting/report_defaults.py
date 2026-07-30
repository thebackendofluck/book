# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Report Defaults — shared formatting and timezone utilities
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
# =============================================================================

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo


def us_date_format(date: datetime) -> str:
    return date.strftime("%m/%d/%Y")


def convert_utc_to_est(date: datetime) -> str:
    """Convert a UTC datetime to Eastern Time formatted string."""
    utc = date.replace(tzinfo=ZoneInfo("UTC"))
    est = utc.astimezone(ZoneInfo("America/New_York"))
    return est.strftime("%m/%d/%Y %H:%M")


def utc_day(date: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) of the UTC day containing the given datetime."""
    day = date.date()
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end   = datetime(day.year, day.month, day.day, 23, 59, 59)
    return start, end


def us_casino_day_in_utc(
    date: datetime,
    start_time: time,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    """
    Convert a US "casino day" (e.g., 06:00 AM to 05:59:59 AM next day in
    local timezone) into UTC timestamps for database queries.

    US regulators define "gaming days" that start at a specific local time
    (commonly 06:00 AM Eastern), not at midnight.
    """
    local_start = datetime(
        date.year, date.month, date.day,
        start_time.hour, start_time.minute,
    ).replace(tzinfo=timezone)

    local_end = (local_start + timedelta(days=1) - timedelta(microseconds=1))

    utc_start = local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_end   = local_end.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return utc_start, utc_end


def us_casino_month_in_utc(
    date: datetime,
    start_time: time,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    """
    Casino month: from start_time on the 1st of the previous month to
    start_time on the 1st of the current month.
    """
    from dateutil.relativedelta import relativedelta  # type: ignore
    local_end   = datetime(date.year, date.month, date.day, start_time.hour, start_time.minute).replace(tzinfo=timezone)
    local_start = (local_end - relativedelta(months=1))

    utc_start = local_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_end   = (local_end - timedelta(microseconds=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return utc_start, utc_end


def format_dob(dob: Optional[datetime]) -> str:
    if dob is None:
        return "NA"
    try:
        return dob.strftime("%m/%d/%Y")
    except Exception:
        return "NA"


def hour_difference(date_a: datetime, date_b: datetime) -> float:
    seconds = (date_b - date_a).total_seconds()
    return seconds / 3600
