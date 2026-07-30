# Companion code for "The Backend of Luck" - Chapter 28a, Distributed Systems Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Event subscriber using Redis Pub/Sub.

Usage:
    import threading
    from app.events.subscriber import subscribe

    def handler(channel, data):
        print(f"[{channel}] {data}")

    t = threading.Thread(
        target=subscribe,
        args=(["player.events", "wallet.transactions"], handler),
        daemon=True,
    )
    t.start()
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from app.redis_client import get_redis

logger = logging.getLogger(__name__)


def subscribe(
    channels: list[str],
    callback: Callable[[str, dict[str, Any]], None],
) -> None:
    """
    Subscribe to one or more Redis Pub/Sub channels.
    Blocks the calling thread. Run in a daemon thread.
    """
    r = get_redis()
    pubsub = r.pubsub()
    pubsub.subscribe(*channels)
    logger.info("Subscribed to channels: %s", channels)

    for message in pubsub.listen():
        if message["type"] != "message":
            continue
        channel = message["channel"]
        try:
            data = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError):
            data = {"raw": message["data"]}

        try:
            callback(channel, data)
        except Exception:
            logger.exception("Error in subscriber callback for channel %s", channel)
