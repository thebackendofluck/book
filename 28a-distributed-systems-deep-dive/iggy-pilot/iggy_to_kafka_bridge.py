#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 28a, Distributed Systems Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
import urllib3
from kafka import KafkaProducer


IGGY_BASE_URL = os.getenv("IGGY_BASE_URL", "https://iggy-api.example.internal").rstrip("/")
IGGY_USERNAME = os.environ["IGGY_ROOT_USERNAME"]
IGGY_PASSWORD = os.environ["IGGY_ROOT_PASSWORD"]
IGGY_STREAM_NAME = os.getenv("IGGY_STREAM_NAME", "casino-ops")
IGGY_TOPIC_NAME = os.getenv("IGGY_TOPIC_NAME", "casino-events")
IGGY_PARTITION_ID = int(os.getenv("IGGY_PARTITION_ID", "0"))
IGGY_CONSUMER_ID = int(os.getenv("IGGY_CONSUMER_ID", "42"))
IGGY_POLL_COUNT = int(os.getenv("IGGY_POLL_COUNT", "100"))
IGGY_POLL_INTERVAL_SECONDS = float(os.getenv("IGGY_POLL_INTERVAL_SECONDS", "2"))
IGGY_OFFSET_FILE = Path(os.getenv("IGGY_OFFSET_FILE", ".iggy-offset"))
IGGY_VERIFY_TLS = os.getenv("IGGY_VERIFY_TLS", "false").lower() in {"1", "true", "yes"}

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "game-events")
KAFKA_CLIENT_ID = os.getenv("KAFKA_CLIENT_ID", "iggy-casino-bridge")


if not IGGY_VERIFY_TLS:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class IggyClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.verify = IGGY_VERIFY_TLS
        self.token = self.login()
        self.session.headers.update({"authorization": f"Bearer {self.token}"})
        self.stream_id = self.find_id("/streams", IGGY_STREAM_NAME)
        self.topic_id = self.find_id(f"/streams/{self.stream_id}/topics", IGGY_TOPIC_NAME)

    def login(self) -> str:
        response = self.session.post(
            f"{IGGY_BASE_URL}/users/login",
            json={"username": IGGY_USERNAME, "password": IGGY_PASSWORD},
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        token = body.get("token") or body.get("access_token") or body.get("jwt")
        if not token:
            raise RuntimeError("Iggy login returned no token")
        return token

    def find_id(self, path: str, name: str) -> int:
        response = self.session.get(f"{IGGY_BASE_URL}{path}", timeout=10)
        response.raise_for_status()
        for item in response.json():
            if item.get("name") == name:
                return int(item["id"])
        raise RuntimeError(f"Iggy object not found: {name}")

    def poll(self, offset: int) -> list[dict[str, Any]]:
        params = {
            "consumer_id": IGGY_CONSUMER_ID,
            "partition_id": IGGY_PARTITION_ID,
            "kind": "offset",
            "value": offset,
            "count": IGGY_POLL_COUNT,
            "auto_commit": "false",
        }
        response = self.session.get(
            f"{IGGY_BASE_URL}/streams/{self.stream_id}/topics/{self.topic_id}/messages",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        return response.json().get("messages", [])


def read_offset() -> int:
    if not IGGY_OFFSET_FILE.exists():
        return 0
    value = IGGY_OFFSET_FILE.read_text(encoding="utf-8").strip()
    return int(value or "0")


def write_offset(offset: int) -> None:
    IGGY_OFFSET_FILE.write_text(f"{offset}\n", encoding="utf-8")


def decode_payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    if not payload:
        raise ValueError("message has no payload")
    decoded = base64.b64decode(payload).decode("utf-8")
    return json.loads(decoded)


def message_offset(message: dict[str, Any], fallback: int) -> int:
    header = message.get("header") or {}
    value = message.get("offset", header.get("offset", fallback))
    return int(value)


def kafka_key(event: dict[str, Any]) -> bytes:
    key = event.get("event_id") or event.get("player_id") or event.get("round_id")
    return str(key or "iggy-casino-event").encode("utf-8")


def main() -> None:
    iggy = IggyClient()
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS.split(","),
        client_id=KAFKA_CLIENT_ID,
        value_serializer=lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8"),
    )
    offset = read_offset()

    while True:
        messages = iggy.poll(offset)
        if not messages:
            time.sleep(IGGY_POLL_INTERVAL_SECONDS)
            continue

        for index, message in enumerate(messages):
            current_offset = message_offset(message, offset + index)
            event = decode_payload(message)
            event.setdefault("source_stream", IGGY_STREAM_NAME)
            event.setdefault("source_topic", IGGY_TOPIC_NAME)
            event.setdefault("source_offset", current_offset)
            producer.send(KAFKA_TOPIC, key=kafka_key(event), value=event)
            offset = current_offset + 1

        producer.flush()
        write_offset(offset)
        print(json.dumps({"forwarded": len(messages), "next_offset": offset, "kafka_topic": KAFKA_TOPIC}))


if __name__ == "__main__":
    main()
