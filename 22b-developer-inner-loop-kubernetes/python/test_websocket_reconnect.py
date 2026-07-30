# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Async WebSocket test: validates live-casino stream reconnect logic.

Requirements:
    pip install websockets pytest pytest-asyncio

Usage:
    # Stand up the game-engine WebSocket service first, then:
    python3 test_websocket_reconnect.py      # direct invocation
    pytest test_websocket_reconnect.py       # under pytest

The test connects to the game-engine WebSocket endpoint, starts a hand,
force-closes the connection, reconnects, and asserts the hand state is
fully restored within the reconnect response.

When the target service is not reachable (e.g. running the full repo
suite on CI without the chapter-22b stack) the test is automatically
skipped so it never becomes a flaky cross-chapter blocker.
"""
from __future__ import annotations

import asyncio
import json
import socket

import pytest

try:
    import websockets
except ImportError:  # pragma: no cover -- dependency absent
    websockets = None  # type: ignore[assignment]


WS_HOST = "localhost"
WS_PORT = 8082
WS_URI = f"ws://{WS_HOST}:{WS_PORT}/ws/live-casino/table-001"


def _service_is_up(host: str = WS_HOST, port: int = WS_PORT) -> bool:
    """Probe the TCP port so we can skip cleanly when the game
    engine isn't running."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_reconnect() -> None:
    if websockets is None:
        pytest.skip("websockets library not installed")
    if not _service_is_up():
        pytest.skip(
            f"game-engine WebSocket not reachable at {WS_HOST}:{WS_PORT}; "
            "start the chapter-22b stack to run this integration test."
        )

    print("Connecting...")
    async with websockets.connect(WS_URI) as ws:
        await ws.send(json.dumps({
            "type": "auth",
            "player_id": "test-player-001",
            "session_token": "dev-test-token",
        }))

        auth_response = await ws.recv()
        print(f"Auth: {auth_response}")

        hand_id: str | None = None
        while True:
            msg = json.loads(await ws.recv())
            print(f"Received: {msg['type']}")
            if msg["type"] == "hand_started":
                hand_id = msg["hand_id"]
                print(f"Hand started: {hand_id}")
                break

        assert hand_id is not None, "Server never sent a hand_started event"

        print("Simulating connection drop...")
        await ws.close()

    print("Reconnecting after 1 second...")
    await asyncio.sleep(1)

    async with websockets.connect(WS_URI) as ws:
        await ws.send(json.dumps({
            "type": "auth",
            "player_id": "test-player-001",
            "session_token": "dev-test-token",
            "resume_hand": hand_id,
        }))

        reconnect_response = json.loads(await ws.recv())
        print(f"Reconnect response: {reconnect_response}")

        assert reconnect_response["type"] == "hand_resumed", (
            f"Expected hand_resumed, got {reconnect_response['type']}"
        )
        assert reconnect_response["hand_id"] == hand_id, \
            "Wrong hand ID on resume"
        assert reconnect_response["state"]["cards"] is not None, \
            "Card state not restored"

        print("PASS: hand state restored correctly on reconnect")


if __name__ == "__main__":
    asyncio.run(test_reconnect())
