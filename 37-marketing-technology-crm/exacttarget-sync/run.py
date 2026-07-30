# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""CLI entry point for the exacttarget-sync pipeline.

Python port of Run.scala referenced in chapter 37. This is the binary
that runs under a cron timer once per night to drive the full
export/import cycle for every configured brand. The CLI accepts:

    run.py --brand acmecasino --tasks playersExport,unsubBouncesImport
    run.py --brand acmecasino --tasks playersExport --fullRange
    run.py --brand acmecasino --tasks playersExport --tokenized

A task registry maps task names to constructor callables. Each task
runs through `Task.run()` so a failure in one does not prevent the
others from running. The CLI exit code is 0 if every task succeeded
and 1 if any task reported an error -- the cron mail wrapper uses
the exit code to decide whether to page an on-call engineer.

This module deliberately does not instantiate SFTP clients, database
connections, or the OpsGenie alerter. Those dependencies are created
by a thin bootstrap layer that is production-specific and wired in
via the `task_factory` argument to `main()`, which keeps the CLI
testable without any infrastructure.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SyncConfig, load_config_from_env  # noqa: E402
from task import Task, TaskResult  # noqa: E402

LOG = logging.getLogger("exacttarget_sync.run")


TaskFactory = Callable[[SyncConfig, str, argparse.Namespace], Task]


@dataclass
class CliOutcome:
    """Aggregate result of a CLI run across all requested tasks."""

    successes: list[TaskResult]
    failures: list[TaskResult]

    @property
    def exit_code(self) -> int:
        return 0 if not self.failures else 1

    def summary(self) -> str:
        lines = [
            f"exacttarget-sync: {len(self.successes)} ok, {len(self.failures)} failed",
        ]
        for r in self.successes:
            lines.append(f"  OK   {r.task_name} ({r.duration_seconds:.2f}s)")
        for r in self.failures:
            lines.append(f"  FAIL {r.task_name}: {r.error_message}")
        return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exacttarget-sync",
        description="Bidirectional sync between the player database and Salesforce Marketing Cloud",
    )
    parser.add_argument(
        "--brand",
        required=True,
        help="Brand id (must be in EXACTTARGET_BRANDS)",
    )
    parser.add_argument(
        "--tasks",
        required=True,
        help="Comma-separated list of task names to run (e.g. playersExport,unsubBouncesImport)",
    )
    parser.add_argument(
        "--fullRange",
        action="store_true",
        help="Bypass incremental window and export the full player table",
    )
    parser.add_argument(
        "--tokenized",
        action="store_true",
        help="Replace email local parts with numeric user ids for GDPR compliance",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    config: SyncConfig | None = None,
    task_factories: dict[str, TaskFactory] | None = None,
) -> CliOutcome:
    """Parse args, build tasks, run them, return an aggregate outcome.

    The `task_factories` dict is injected by the bootstrap in
    production (it maps a task name to a callable that builds an
    instance wired up with real SFTP/DB/alerter dependencies). Tests
    supply a dict of in-memory task factories so the CLI can be
    exercised without network access.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = config or load_config_from_env()
    factories = task_factories or {}

    task_names = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if not task_names:
        raise SystemExit("at least one task is required via --tasks")

    tasks: list[Task] = []
    for name in task_names:
        factory = factories.get(name)
        if factory is None:
            raise SystemExit(
                f"unknown task {name!r}; registered: {', '.join(sorted(factories)) or '(none)'}"
            )
        tasks.append(factory(cfg, args.brand, args))

    successes: list[TaskResult] = []
    failures: list[TaskResult] = []
    for task in tasks:
        LOG.info("starting %s", task.name)
        result = task.run()
        if result.success:
            successes.append(result)
        else:
            failures.append(result)

    outcome = CliOutcome(successes=successes, failures=failures)
    print(outcome.summary())
    return outcome


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    sys.exit(main().exit_code)
