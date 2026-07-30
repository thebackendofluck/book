# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Base Task class with lifecycle hooks and OpsGenie alerting.

Python port of Task.scala referenced in chapter 37, section "ExactTarget
(Salesforce Marketing Cloud) Sync". Every operational task that runs as
part of the exacttarget-sync CLI subclasses `Task` and overrides
`do_task`. The `before`/`after` hooks provide a predictable place to
record start/stop timestamps, acquire locks, or flush metrics, and the
base class invokes the OpsGenie alerter if `do_task` raises.

Design goals
------------

1. **Zero third-party dependencies in the core** -- the alerter is a
   Protocol so unit tests can inject an in-memory double and the
   production code plugs in a real OpsGenie HTTP client.
2. **Deterministic lifecycle** -- `run()` always calls `before`, then
   `do_task`, then `after`, and an alert is raised iff `do_task`
   raised. The `after` hook runs even on failure (equivalent to a
   `try/finally`) so clean-up code does not get skipped.
3. **Stable exit code** -- `run()` returns 0 on success and 1 on any
   exception from `do_task` or the `after` hook. `before` failures
   are also reported as 1 because a task that cannot initialise is
   no different operationally from a task that fails mid-way.
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

LOG = logging.getLogger("exacttarget_sync.task")


class Alerter(Protocol):
    """Minimal interface for OpsGenie-style alerting.

    Production code wraps the OpsGenie HTTP client behind this Protocol
    so that tasks never see a network dependency at construction time.
    """

    def alert(self, task_name: str, message: str, *, priority: str = "P3") -> None:
        """Fire an alert. Implementations must not raise -- an alerter
        that throws would prevent `run()` from returning a clean exit
        code to the shell.
        """


class NullAlerter:
    """Alerter that records calls to a list. Used by tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def alert(self, task_name: str, message: str, *, priority: str = "P3") -> None:
        self.calls.append((task_name, message, priority))


@dataclass
class TaskResult:
    """Outcome of a single `Task.run()` invocation."""

    task_name: str
    success: bool
    duration_seconds: float
    error_message: str | None = None


class Task(abc.ABC):
    """Abstract base for every task registered with the CLI.

    Subclasses must override `do_task`. The `before` and `after` hooks
    default to no-ops; override them to record state or clean up.
    """

    #: Priority used when an alert is raised on failure.
    alert_priority: str = "P2"

    def __init__(self, *, alerter: Alerter | None = None) -> None:
        self.alerter = alerter or NullAlerter()

    @property
    def name(self) -> str:
        return type(self).__name__

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def before(self) -> None:
        """Run before `do_task`. Use to acquire locks, record start
        timestamps, or validate preconditions. Default is a no-op.
        """

    @abc.abstractmethod
    def do_task(self) -> None:
        """The real work. Subclasses must implement this."""

    def after(self) -> None:
        """Run after `do_task`, regardless of whether it raised. Use
        for clean-up, lock release, and final metric flushes.
        """

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------

    def run(self) -> TaskResult:
        """Execute the full lifecycle and return a structured result.

        Never raises -- any exception from `before`, `do_task` or
        `after` is captured, alerted, and reported via the return
        value so the CLI driver can aggregate results across multiple
        tasks without having to wrap each call in its own try/except.
        """
        start = time.monotonic()
        error: str | None = None
        success = True

        try:
            LOG.info("task %s: before", self.name)
            self.before()
        except Exception as err:  # noqa: BLE001 -- caught by design
            success = False
            error = f"before() failed: {err}"
            self._safely_alert(error)
            # After hook still runs below so clean-up happens.

        if success:
            try:
                LOG.info("task %s: do_task", self.name)
                self.do_task()
            except Exception as err:  # noqa: BLE001
                success = False
                error = f"do_task() failed: {err}"
                self._safely_alert(error)

        try:
            LOG.info("task %s: after", self.name)
            self.after()
        except Exception as err:  # noqa: BLE001
            # An after-failure never promotes a previous success to
            # success, but it does downgrade an already-failed result
            # from "failed in do_task" to "failed in do_task AND
            # after", which is strictly more information.
            new_error = f"after() failed: {err}"
            if success:
                success = False
                error = new_error
                self._safely_alert(error)
            else:
                error = f"{error}; {new_error}"

        duration = time.monotonic() - start
        return TaskResult(
            task_name=self.name,
            success=success,
            duration_seconds=duration,
            error_message=error,
        )

    def _safely_alert(self, message: str) -> None:
        try:
            self.alerter.alert(self.name, message, priority=self.alert_priority)
        except Exception as err:  # noqa: BLE001
            LOG.error("alerter raised on task %s: %s", self.name, err)
