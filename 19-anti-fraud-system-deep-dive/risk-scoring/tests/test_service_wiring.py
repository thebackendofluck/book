# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Tests for service_wiring.py -- the guard that keeps the risk-scoring
service from silently running on always-zero stub services in production.
"""
from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import cast

import pytest

_SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str):
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, _SERVICE_DIR / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


service_wiring = _load_local_module("service_wiring", "service_wiring.py")


class _Port:
    """Stand-in abstract base for a service port."""


class _StubImpl(_Port):
    pass


class _RealImpl(_Port):
    pass


class _WrongTypeImpl:
    """Deliberately does NOT implement _Port."""


def test_raises_when_no_dotted_path_and_stubs_disallowed():
    with pytest.raises(RuntimeError, match="stub fallback is disabled"):
        service_wiring.load_service(
            "SOME_UNSET_ENV_VAR", _Port, _StubImpl,
            allow_stubs=False, is_test_env=False,
        )


def test_returns_stub_when_stubs_explicitly_allowed():
    result = service_wiring.load_service(
        "SOME_UNSET_ENV_VAR", _Port, _StubImpl,
        allow_stubs=True, is_test_env=False,
    )
    assert isinstance(result, _StubImpl)


def test_returns_stub_under_test_env_even_without_allow_flag():
    result = service_wiring.load_service(
        "SOME_UNSET_ENV_VAR", _Port, _StubImpl,
        allow_stubs=False, is_test_env=True,
    )
    assert isinstance(result, _StubImpl)


def test_loads_real_adapter_from_dotted_path(monkeypatch):
    monkeypatch.setenv("SOME_ENV_VAR", f"{__name__}._RealImpl")
    result = service_wiring.load_service(
        "SOME_ENV_VAR", _Port, _StubImpl,
        allow_stubs=False, is_test_env=False,
    )
    assert isinstance(result, _RealImpl)


def test_dotted_path_type_mismatch_raises(monkeypatch):
    monkeypatch.setenv("SOME_ENV_VAR", f"{__name__}._WrongTypeImpl")
    with pytest.raises(RuntimeError, match="does not implement"):
        service_wiring.load_service(
            "SOME_ENV_VAR", _Port, _StubImpl,
            allow_stubs=False, is_test_env=False,
        )


def test_env_flag_recognises_truthy_values(monkeypatch):
    for val in ("1", "true", "True", "yes", "on"):
        monkeypatch.setenv("FLAG", val)
        assert service_wiring.env_flag("FLAG") is True


def test_env_flag_defaults_false_when_unset(monkeypatch):
    monkeypatch.delenv("FLAG", raising=False)
    assert service_wiring.env_flag("FLAG") is False


def test_is_test_env_true_under_pytest():
    # PYTEST_CURRENT_TEST is set by pytest itself while a test is running.
    assert service_wiring.is_test_env() is True
