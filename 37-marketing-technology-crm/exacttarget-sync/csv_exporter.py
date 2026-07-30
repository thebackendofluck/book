# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Generic CSV writer with configurable file splitting.

Python port of CsvExporter.scala referenced in chapter 37. The class
takes a destination directory, a base filename, a line-count split
limit, and a stream of rows, and writes one or more CSV files named
`basename.001.csv`, `basename.002.csv`, ... each containing at most
`split_line_count` data rows plus a single header line.

The splitting is a hard requirement for the ExactTarget SFTP upload:
files above ~500,000 rows frequently time out on the receiving side
and must be retransmitted, which doubles the import window and risks
missing the next marketing campaign fire. Rather than guess at the
limit per brand, the splitter enforces it upstream.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CsvExportResult:
    """Summary of a single `export` invocation."""

    files_written: list[Path]
    total_rows: int

    @property
    def file_count(self) -> int:
        return len(self.files_written)


class CsvExporter:
    """Write an iterable of dict rows to one or more split CSV files.

    Parameters
    ----------
    destination_dir:
        Directory to write to. Created if it does not exist.
    base_name:
        Stem used to build the split file names. Do not include
        `.csv` -- the exporter appends the suffix itself.
    header:
        Ordered list of column names. Every row must supply a value
        for every header field; missing keys raise KeyError.
    split_line_count:
        Maximum data rows per file (the header does not count against
        this limit). Defaults to 500,000 -- the documented safe size
        for ExactTarget SFTP uploads. Set to 0 to disable splitting.
    newline:
        Line terminator. ExactTarget accepts both LF and CRLF; the
        default matches the platform-native convention of the host
        running the exporter.
    """

    def __init__(
        self,
        destination_dir: str | Path,
        base_name: str,
        header: list[str],
        *,
        split_line_count: int = 500_000,
        newline: str = "",
    ) -> None:
        if not header:
            raise ValueError("header cannot be empty")
        if split_line_count < 0:
            raise ValueError("split_line_count cannot be negative")
        self._dir = Path(destination_dir)
        self._base_name = base_name
        self._header = list(header)
        self._split = split_line_count
        self._newline = newline

    def export(self, rows: Iterable[dict[str, object]]) -> CsvExportResult:
        """Write every row in `rows` to split files.

        The rows are consumed lazily so that callers can stream in
        from a database cursor without materialising the whole dataset
        in memory -- important because a large operator exports tens
        of millions of player rows per night.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        files_written: list[Path] = []
        total_rows = 0

        chunk_iter = self._chunked(iter(rows))
        for index, chunk in enumerate(chunk_iter, start=1):
            if not chunk:
                continue
            file_path = self._file_for_index(index)
            with file_path.open("w", newline=self._newline, encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=self._header, extrasaction="ignore")
                writer.writeheader()
                for row in chunk:
                    self._check_row(row)
                    writer.writerow(row)
                    total_rows += 1
            files_written.append(file_path)

        return CsvExportResult(files_written=files_written, total_rows=total_rows)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _file_for_index(self, index: int) -> Path:
        if self._split == 0:
            return self._dir / f"{self._base_name}.csv"
        return self._dir / f"{self._base_name}.{index:03d}.csv"

    def _chunked(self, rows: Iterator[dict[str, object]]) -> Iterator[list[dict[str, object]]]:
        """Yield lists of up to `split_line_count` rows at a time."""
        if self._split == 0:
            buf: list[dict[str, object]] = list(rows)
            yield buf
            return

        buf = []
        for row in rows:
            buf.append(row)
            if len(buf) >= self._split:
                yield buf
                buf = []
        if buf:
            yield buf

    def _check_row(self, row: dict[str, object]) -> None:
        missing = [h for h in self._header if h not in row]
        if missing:
            raise KeyError(
                f"row missing required header fields: {', '.join(missing)}"
            )


def cleanup_old_files(directory: str | Path, *, retention_days: int, pattern: str = "*.csv") -> int:
    """Delete CSV files older than `retention_days` in `directory`.

    Returns the number of files deleted. Used by `PlayersExportTask`
    to enforce the 3-day retention window documented in chapter 37.
    """
    import time

    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")

    dirpath = Path(directory)
    if not dirpath.is_dir():
        return 0

    cutoff = time.time() - (retention_days * 86_400)
    deleted = 0
    for path in dirpath.glob(pattern):
        if path.is_file() and path.stat().st_mtime < cutoff:
            try:
                os.remove(path)
                deleted += 1
            except OSError:
                pass
    return deleted
