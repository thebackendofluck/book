# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Service wiring guard for the risk-scoring app.

The stub services in main.py (`_StubUserEventsService`, `_StubFlagsService`,
`_StubMatrixScoreRepository`) return 0/None/False for everything. If they
ever ended up wired into a running instance without anyone noticing, every
velocity/deposit/AML risk rule would silently evaluate against zeros --
the service would look healthy while never actually flagging anything.

`_load_service` refuses that outcome: a real adapter must be configured via
a dotted `module.ClassName` env var, and the in-memory stub is only used
when explicitly allowed (local dev) or when running under pytest.
"""
from __future__ import annotations

import importlib
import os


def env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def is_test_env() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or os.environ.get(
        "APP_ENV", ""
    ).strip().lower() in ("test", "dev", "development")


def load_service(
    env_var: str,
    base_cls: type,
    stub_factory,
    *,
    allow_stubs: bool,
    is_test_env: bool,
):
    """Wire a real service adapter from a dotted `module.ClassName` path in
    `env_var`, refusing to fall back to the always-zero in-memory stub
    unless stubs are explicitly permitted (dev) or we're under test.
    """
    dotted = os.environ.get(env_var)
    if dotted:
        module_name, _, class_name = dotted.rpartition(".")
        if not module_name:
            raise RuntimeError(f"{env_var}={dotted!r} must be a dotted module.ClassName path")
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        instance = cls()
        if not isinstance(instance, base_cls):
            raise RuntimeError(f"{env_var}={dotted} does not implement {base_cls.__name__}")
        return instance
    if allow_stubs or is_test_env:
        return stub_factory()
    raise RuntimeError(
        f"No real service configured for {env_var} and stub fallback is disabled. "
        f"Set {env_var} to a dotted class path (module.ClassName implementing "
        f"{base_cls.__name__}), or set RISK_SCORING_ALLOW_STUBS=true for local "
        f"development only -- production must never score risk rules against "
        f"in-memory stubs that always return zero."
    )
