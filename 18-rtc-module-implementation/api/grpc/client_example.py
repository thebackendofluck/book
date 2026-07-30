#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gRPC Client Example for RTC Timestamp Service
===============================================

Demonstrates how game servers integrate with the RTC timestamp service
via gRPC for high-throughput timestamp operations.

This example covers:
    1. Unary RPC: Get a single signed timestamp
    2. Batch RPC: Get multiple timestamps efficiently
    3. Server streaming: Continuous timestamp stream
    4. Validation: Verify timestamp signatures
    5. Error handling and retry logic

Prerequisites:
    pip install grpcio grpcio-tools protobuf

    # Generate Python stubs from proto:
    python -m grpc_tools.protoc \\
        -I. \\
        --python_out=. \\
        --grpc_python_out=. \\
        rtc_service.proto

Usage:
    python3 client_example.py --host rtc-service.rtc-system.svc:50051
    python3 client_example.py --host localhost:50051 --insecure
"""

import argparse
import logging
import sys
import time
import uuid
from concurrent import futures
from typing import Iterator, Optional

# gRPC imports
try:
    import grpc
except ImportError:
    print("ERROR: grpcio not installed. Run: pip install grpcio grpcio-tools")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rtc-grpc-client")


# ---------------------------------------------------------------------------
# Simulated proto stubs (in production, these are generated from .proto)
# ---------------------------------------------------------------------------
# NOTE: In a real deployment, you would import generated stubs:
#   from casino.rtc.v1 import rtc_service_pb2 as rtc_pb2
#   from casino.rtc.v1 import rtc_service_pb2_grpc as rtc_grpc
#
# The classes below simulate the generated types for illustration.

class TimestampRequest:
    """Simulated proto message."""
    def __init__(self, request_id="", metadata=None, require_consensus=True,
                 precision=4, event_type=0):
        self.request_id = request_id
        self.metadata = metadata or {}
        self.require_consensus = require_consensus
        self.precision = precision
        self.event_type = event_type

    def SerializeToString(self):
        import json
        return json.dumps({
            "request_id": self.request_id,
            "metadata": self.metadata,
            "require_consensus": self.require_consensus,
        }).encode()


class TimestampResponse:
    """Simulated proto response."""
    def __init__(self, unix_seconds=0, nanos=0, iso8601="", signature="",
                 confidence=0.0, drift_ms=0.0, source="", metadata=None,
                 consensus_round_id=""):
        self.unix_seconds = unix_seconds
        self.nanos = nanos
        self.iso8601 = iso8601
        self.signature = signature
        self.confidence = confidence
        self.drift_ms = drift_ms
        self.source = source
        self.metadata = metadata or {}
        self.consensus_round_id = consensus_round_id


# ---------------------------------------------------------------------------
# RTC gRPC Client
# ---------------------------------------------------------------------------
class RTCClient:
    """
    High-level gRPC client for the RTC Timestamp Service.

    Provides a clean API for game servers to obtain and validate
    hardware-backed timestamps with automatic retry logic and
    connection management.

    Example:
        client = RTCClient("rtc-service.rtc-system.svc:50051")
        ts = client.get_timestamp(game_id="slots-fortune-tiger")
        print(f"Timestamp: {ts.iso8601}, Signature: {ts.signature}")
    """

    def __init__(
        self,
        host: str = "localhost:50051",
        secure: bool = True,
        ca_cert_path: Optional[str] = None,
        client_cert_path: Optional[str] = None,
        client_key_path: Optional[str] = None,
        timeout_ms: int = 1000,
        max_retries: int = 3,
    ):
        """
        Initialize the RTC gRPC client.

        Args:
            host: gRPC server address (host:port)
            secure: Use TLS (required in production)
            ca_cert_path: CA certificate for server verification
            client_cert_path: Client certificate for mTLS
            client_key_path: Client key for mTLS
            timeout_ms: Default RPC timeout in milliseconds
            max_retries: Maximum retry attempts for failed RPCs
        """
        self.host = host
        self.timeout_s = timeout_ms / 1000
        self.max_retries = max_retries

        # Configure channel
        options = [
            ("grpc.keepalive_time_ms", 10000),
            ("grpc.keepalive_timeout_ms", 5000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            # Enable retries
            ("grpc.enable_retries", 1),
            ("grpc.service_config", '{"retryPolicy": {'
             '"maxAttempts": 3, '
             '"initialBackoff": "0.1s", '
             '"maxBackoff": "1s", '
             '"backoffMultiplier": 2, '
             '"retryableStatusCodes": ["UNAVAILABLE", "DEADLINE_EXCEEDED"]}}'),
        ]

        if secure:
            # Production: mTLS with certificates
            if ca_cert_path and client_cert_path and client_key_path:
                with open(ca_cert_path, "rb") as f:
                    ca_cert = f.read()
                with open(client_cert_path, "rb") as f:
                    client_cert = f.read()
                with open(client_key_path, "rb") as f:
                    client_key = f.read()

                credentials = grpc.ssl_channel_credentials(  # ty:ignore[unresolved-attribute]
                    root_certificates=ca_cert,
                    private_key=client_key,
                    certificate_chain=client_cert,
                )
                self.channel = grpc.secure_channel(host, credentials, options=options)  # ty:ignore[unresolved-attribute]
            else:
                # TLS without client certs
                self.channel = grpc.secure_channel(  # ty:ignore[unresolved-attribute]
                    host,
                    grpc.ssl_channel_credentials(),  # ty:ignore[unresolved-attribute]
                    options=options,
                )
        else:
            # Development only: insecure channel
            logger.warning("Using insecure gRPC channel (development only)")
            self.channel = grpc.insecure_channel(host, options=options)  # ty:ignore[unresolved-attribute]

        logger.info(f"Connected to RTC service at {host} (secure={secure})")

    def close(self):
        """Close the gRPC channel."""
        self.channel.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -----------------------------------------------------------------------
    # Timestamp Operations
    # -----------------------------------------------------------------------
    def get_timestamp(
        self,
        game_id: str = "",
        session_id: str = "",
        event_type: str = "EVENT_UNSPECIFIED",
        require_consensus: bool = True,
    ) -> dict:
        """
        Get a single signed timestamp.

        This is the most common operation. Game servers call this
        at the start and end of each game round, and for each
        financial transaction.

        Args:
            game_id: Game identifier for audit correlation
            session_id: Player session identifier
            event_type: Type of game event being timestamped
            require_consensus: Require full BFT consensus

        Returns:
            Dictionary with timestamp fields

        Example:
            ts = client.get_timestamp(
                game_id="blackjack-classic",
                event_type="EVENT_ROUND_START"
            )
            # Store ts["signature"] alongside the game event
        """
        request_id = str(uuid.uuid4())
        metadata = {
            "game_id": game_id,
            "session_id": session_id,
            "request_id": request_id,
        }

        # In production, this calls the generated stub:
        #   stub = rtc_grpc.RTCTimestampServiceStub(self.channel)
        #   response = stub.GetTimestamp(request, timeout=self.timeout_s)

        # Simulated response for illustration
        now = time.time()
        response = {
            "unix_seconds": int(now),
            "nanos": int((now % 1) * 1_000_000_000),
            "iso8601": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + "Z",
            "source": "rtc-dc-east-1-01",
            "signature": f"hmac-sha256:{request_id[:16]}",
            "confidence": 0.9875,
            "drift_ms": 0.042,
            "metadata": metadata,
            "consensus_round_id": f"consensus-{int(now)}",
        }

        logger.debug(f"Timestamp: {response['iso8601']} confidence={response['confidence']}")
        return response

    def get_batch_timestamps(
        self,
        count: int,
        interval_ms: int = 0,
        game_id: str = "",
    ) -> list:
        """
        Get multiple timestamps in a single RPC.

        More efficient than multiple get_timestamp() calls when you
        need several timestamps (e.g., for batch round processing).

        Args:
            count: Number of timestamps (max 100)
            interval_ms: Delay between timestamps (0 = immediate)
            game_id: Game identifier

        Returns:
            List of timestamp dictionaries
        """
        if count > 100:
            raise ValueError("Maximum batch size is 100")

        timestamps = []
        for i in range(count):
            ts = self.get_timestamp(game_id=game_id)
            timestamps.append(ts)
            if interval_ms > 0 and i < count - 1:
                time.sleep(interval_ms / 1000)

        logger.info(f"Batch: {count} timestamps generated for game={game_id}")
        return timestamps

    def stream_timestamps(
        self,
        interval_ms: int = 100,
        include_drift: bool = False,
        game_id: str = "",
    ) -> Iterator[dict]:
        """
        Stream timestamps from the server.

        Opens a server-streaming RPC that pushes timestamps at the
        configured interval. Useful for game servers that need
        continuous time synchronization.

        Args:
            interval_ms: Push interval (min 10ms)
            include_drift: Include drift measurements
            game_id: Game identifier

        Yields:
            Timestamp dictionaries

        Example:
            for ts in client.stream_timestamps(interval_ms=50):
                process_game_tick(ts)
        """
        # In production:
        #   request = StreamRequest(interval_ms=interval_ms, ...)
        #   for response in stub.StreamTimestamps(request):
        #       yield response_to_dict(response)

        logger.info(f"Starting timestamp stream (interval={interval_ms}ms)")
        while True:
            yield self.get_timestamp(game_id=game_id)
            time.sleep(interval_ms / 1000)

    # -----------------------------------------------------------------------
    # Validation Operations
    # -----------------------------------------------------------------------
    def validate_timestamp(self, timestamp: dict, tolerance_ms: int = 1000) -> dict:
        """
        Validate a timestamp signature.

        Used during dispute resolution and regulatory audits to
        confirm that a timestamp was genuinely issued by the RTC
        service and has not been tampered with.

        Args:
            timestamp: Previously issued timestamp dictionary
            tolerance_ms: Maximum acceptable drift

        Returns:
            Validation result dictionary
        """
        # In production:
        #   request = ValidationRequest(timestamp=ts, tolerance_ms=tolerance_ms)
        #   response = stub.ValidateTimestamp(request, timeout=self.timeout_s)

        return {
            "valid": True,
            "reason": "Signature verified successfully",
            "actual_drift_ms": timestamp.get("drift_ms", 0.0),
            "key_id": "rtc-key-2024-q1",
        }

    # -----------------------------------------------------------------------
    # Health & Diagnostics
    # -----------------------------------------------------------------------
    def check_health(self) -> dict:
        """
        Check service health.

        Returns:
            Health status dictionary
        """
        # In production, use gRPC Health Checking Protocol
        try:
            ts = self.get_timestamp()
            return {
                "status": "SERVING",
                "latency_ms": 0.5,
                "consensus_confidence": ts.get("confidence", 0),
            }
        except Exception as e:
            return {
                "status": "NOT_SERVING",
                "error": str(e),
            }

    def get_drift(self, module_id: str = "") -> dict:
        """
        Get current drift measurements.

        Args:
            module_id: Specific module (empty = all modules)

        Returns:
            Drift measurement dictionary
        """
        return {
            "modules": [
                {"module_id": "rtc-01", "drift_ms": 0.02, "status": "active"},
                {"module_id": "rtc-02", "drift_ms": 0.05, "status": "active"},
                {"module_id": "rtc-03", "drift_ms": 0.03, "status": "active"},
                {"module_id": "rtc-04", "drift_ms": 0.04, "status": "active"},
            ],
            "median_drift_ms": 0.035,
            "max_drift_ms": 0.05,
            "compliant": True,
        }


# ---------------------------------------------------------------------------
# Game Server Integration Examples
# ---------------------------------------------------------------------------
def example_slot_game_round(client: RTCClient):
    """
    Example: Timestamping a slot machine game round.

    GLI-11 requires that each game round has:
    - Start timestamp (when bet is placed)
    - RNG timestamp (when random outcome is generated)
    - End timestamp (when result is displayed to player)
    - All timestamps signed for non-repudiation
    """
    game_id = "slots-fortune-tiger"
    session_id = str(uuid.uuid4())
    round_id = str(uuid.uuid4())

    print(f"\n--- Slot Game Round: {round_id[:8]}... ---")

    # 1. Round start (bet placement)
    start_ts = client.get_timestamp(
        game_id=game_id,
        session_id=session_id,
        event_type="EVENT_BET_PLACED",
    )
    print(f"  Bet placed at: {start_ts['iso8601']}")

    # 2. RNG execution
    rng_ts = client.get_timestamp(
        game_id=game_id,
        session_id=session_id,
        event_type="EVENT_ROUND_START",
    )
    print(f"  RNG executed at: {rng_ts['iso8601']}")

    # 3. Round end (result display)
    end_ts = client.get_timestamp(
        game_id=game_id,
        session_id=session_id,
        event_type="EVENT_ROUND_END",
    )
    print(f"  Result displayed at: {end_ts['iso8601']}")

    # 4. Store all signatures with the round record
    round_record = {
        "round_id": round_id,
        "game_id": game_id,
        "session_id": session_id,
        "bet_timestamp": start_ts["iso8601"],
        "bet_signature": start_ts["signature"],
        "rng_timestamp": rng_ts["iso8601"],
        "rng_signature": rng_ts["signature"],
        "end_timestamp": end_ts["iso8601"],
        "end_signature": end_ts["signature"],
        "consensus_confidence": min(
            start_ts["confidence"],
            rng_ts["confidence"],
            end_ts["confidence"],
        ),
    }
    print(f"  Consensus confidence: {round_record['consensus_confidence']}")
    print(f"  All 3 timestamps signed for GLI-11 audit trail")


def example_jackpot_hit(client: RTCClient):
    """
    Example: Timestamping a progressive jackpot hit.

    Jackpot events require the highest level of timestamp assurance
    because they involve large financial payouts and are frequently
    subject to regulatory review.
    """
    print("\n--- Progressive Jackpot Hit ---")

    ts = client.get_timestamp(
        game_id="slots-mega-fortune",
        event_type="EVENT_JACKPOT_HIT",
        require_consensus=True,
    )

    print(f"  Jackpot hit at: {ts['iso8601']}")
    print(f"  Confidence: {ts['confidence']}")
    print(f"  Source module: {ts['source']}")
    print(f"  Signature: {ts['signature']}")

    # Validate the timestamp immediately (belt and suspenders)
    validation = client.validate_timestamp(ts)
    print(f"  Validation: {validation['valid']} ({validation['reason']})")


def example_high_throughput(client: RTCClient):
    """
    Example: High-throughput batch timestamping.

    Game aggregation servers processing thousands of rounds per
    second use batch and streaming modes for efficiency.
    """
    print("\n--- High-Throughput Batch Example ---")

    # Get 10 timestamps in a single RPC
    batch = client.get_batch_timestamps(
        count=10,
        game_id="roulette-european",
    )
    print(f"  Generated {len(batch)} timestamps in batch")
    for i, ts in enumerate(batch):
        print(f"    [{i}] {ts['iso8601']} sig={ts['signature'][:20]}...")

    # Stream mode (limited to 5 iterations for demo)
    print("\n  Streaming mode (5 samples):")
    count = 0
    for ts in client.stream_timestamps(interval_ms=200, game_id="crash-aviator"):
        print(f"    Stream [{count}] {ts['iso8601']}")
        count += 1
        if count >= 5:
            break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="RTC gRPC Client Example for Game Server Integration"
    )
    parser.add_argument("--host", default="localhost:50051", help="gRPC server address")
    parser.add_argument("--insecure", action="store_true", help="Use insecure channel")
    parser.add_argument(
        "--example",
        choices=["all", "slot", "jackpot", "batch", "health"],
        default="all",
        help="Which example to run",
    )

    args = parser.parse_args()

    with RTCClient(host=args.host, secure=not args.insecure) as client:
        if args.example in ("all", "health"):
            health = client.check_health()
            print(f"\nService Health: {health['status']}")

            drift = client.get_drift()
            print(f"Median Drift: {drift['median_drift_ms']}ms (compliant: {drift['compliant']})")

        if args.example in ("all", "slot"):
            example_slot_game_round(client)

        if args.example in ("all", "jackpot"):
            example_jackpot_hit(client)

        if args.example in ("all", "batch"):
            example_high_throughput(client)


if __name__ == "__main__":
    main()
