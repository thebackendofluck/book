# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Preload local modules; force-anchored via importlib to survive sibling conftests."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE_DIR = os.path.abspath(os.path.join(HERE, ".."))
_SCRIPTS_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from _preload_local import make_collectstart_hook, preload_local_modules

_LOCAL_MODULES = ('main', 'models', 'alert_dispatcher', 'health_monitor', 'kafka_consumer', 'outbox_service', 'repository')

preload_local_modules(SERVICE_DIR, _LOCAL_MODULES)
pytest_collectstart = make_collectstart_hook(HERE, SERVICE_DIR, _LOCAL_MODULES)
