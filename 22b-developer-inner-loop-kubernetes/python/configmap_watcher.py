# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# wallet-service/app/config/watcher.py
# Example: watching a ConfigMap volume mount for hot config reload

import asyncio
import logging
from pathlib import Path
from watchfiles import awatch

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/etc/config/feature-flags.json")

async def watch_config(app_state):
    """Watch ConfigMap volume mount for changes. No pod restart needed."""
    async for changes in awatch(CONFIG_PATH.parent):
        for change_type, path in changes:
            if Path(path).name == CONFIG_PATH.name:
                app_state.reload_feature_flags()
                logger.info("Feature flags reloaded from ConfigMap (no restart)")
