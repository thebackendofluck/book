# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Entry point for the regulator-revenue-collector.

Runs every state collector concurrently, writes one
{state}.json per regulator into website/public/data/regulator-revenue/,
and writes a top-level index.json with state list + last-collected timestamp.

The Python collector ships its own copy of the JSON under
scripts/chapter-25b/regulator-revenue-collector/output/snapshots/ so it can
be diffed in the repo without forcing a website rebuild.

Usage:
    python run.py [--only NY,NJ] [--no-parse] [--out PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import structlog

from collectors import ALL_COLLECTORS
from collectors.base import StateCollector
from models import ReportFile, StateSnapshot

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
log = structlog.get_logger()


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "output"
# Default website-data path is relative to the dev checkout. When the script
# runs in a container or anywhere without that ancestor tree, fall back to
# DEFAULT_OUTPUT/website-data so nothing crashes.
try:
    WEBSITE_DATA = HERE.parents[2] / "website" / "public" / "data" / "regulator-revenue"
except IndexError:
    WEBSITE_DATA = DEFAULT_OUTPUT / "website-data"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weekly US gaming-regulator revenue report collector")
    p.add_argument("--only", help="Comma-separated state codes (e.g. NY,NJ). Default: all.")
    p.add_argument("--no-parse", action="store_true",
                   help="Skip Excel/PDF summarisation. Faster; only fetches and hashes files.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUTPUT,
                   help="Cache root for downloaded files. Default: ./output/")
    p.add_argument("--website-data", type=Path, default=WEBSITE_DATA,
                   help="Where to write JSON snapshots for the Next.js site to consume.")
    return p.parse_args()


def install_parsers() -> None:
    """Wire parsers/* into the StateCollector base class once at startup."""
    from parsers import summarise_pdf, summarise_xlsx

    async def parse(self, report: ReportFile, file_path: Path) -> list:
        if report.format in ("xlsx", "xls"):
            return summarise_xlsx(file_path)
        if report.format == "pdf":
            return summarise_pdf(file_path)
        return []

    StateCollector.parse = parse  # ty: ignore[method-assign]


async def collect_one(cls: type[StateCollector], output_root: Path) -> StateSnapshot:
    inst = cls(output_root=output_root)
    log.info("collecting", state=inst.state)
    snap = await inst.collect()
    log.info("collected",
             state=inst.state,
             reports=len(snap.reports),
             succeeded=sum(1 for r in snap.reports if not r.fetch_error),
             failed=sum(1 for r in snap.reports if r.fetch_error))
    return snap


async def main_async(args: argparse.Namespace) -> None:
    if not args.no_parse:
        install_parsers()

    only = {s.strip().upper() for s in args.only.split(",")} if args.only else None
    selected = [c for c in ALL_COLLECTORS if not only or c.state in only]

    args.out.mkdir(parents=True, exist_ok=True)
    args.website_data.mkdir(parents=True, exist_ok=True)
    snapshots_dir = args.out / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    snapshots = await asyncio.gather(*(collect_one(c, args.out) for c in selected))

    # Always write per-state files first.
    for s in snapshots:
        body = s.model_dump_json(indent=2)
        (snapshots_dir / f"{s.state}.json").write_text(body)
        (args.website_data / f"{s.state}.json").write_text(body)

    # Rebuild index.json from ALL state files on disk so a partial run
    # (--only NY) doesn't truncate the index back to a single entry.
    def _rebuild_index(target_dir: Path) -> dict:
        all_states = []
        for f in sorted(target_dir.glob("*.json")):
            if f.name == "index.json":
                continue
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            all_states.append({
                "state": data["state"],
                "regulator": data["regulator"],
                "source_url": data["source_url"],
                "report_count": len(data.get("reports", [])),
                "successful": sum(1 for r in data.get("reports", []) if not r.get("fetch_error")),
                "collected_at": data["collected_at"],
            })
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "states": all_states,
        }

    snap_index = _rebuild_index(snapshots_dir)
    web_index = _rebuild_index(args.website_data)
    (snapshots_dir / "index.json").write_text(json.dumps(snap_index, indent=2))
    (args.website_data / "index.json").write_text(json.dumps(web_index, indent=2))

    # Optional time-series sink for the dashboard. Skipped silently when
    # DATABASE_URL isn't set (e.g. local dev runs without the docker stack).
    db_inserts = 0
    try:
        from db_writer import write_all
        db_inserts = write_all(snapshots)
    except ImportError:
        pass

    log.info("done",
             out=str(args.out),
             website_data=str(args.website_data),
             total_states=len(snapshots),
             total_reports=sum(len(s.reports) for s in snapshots),
             db_inserts=db_inserts)


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
