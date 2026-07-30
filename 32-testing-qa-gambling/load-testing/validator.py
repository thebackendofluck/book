# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

from __future__ import annotations

import json
import logging
from locust_logger import LOGGER
import os
import random
import time
import uuid
import string
import datetime
from typing import Any, List

from locust import events
from locust import HttpUser, TaskSet, SequentialTaskSet, task, between
from locust.contrib.fasthttp import FastHttpUser
from locust.exception import StopUser


KAMBI_OFFERING_URL = os.getenv("KAMBI_OFFERING_URL", "https://offering.example.com")
KAMBI_PLAYER_URL = os.getenv("KAMBI_PLAYER_URL", "https://auth.example.com")
KAMBI_OFFERING_ID = os.getenv("KAMBI_OFFERING_ID", "demo-offering")
WH_BASE_URL = os.getenv(
    "WH_BASE_URL", "https://platform.live.casino-platform.example.com/api"
)


def random_pause(low, high):
    time.sleep(random.randrange(low, high))


failed_records = []


class ValidatorTasks(TaskSet):
    event_ids: List[int]
    user_correlation_id: str
    wallet: float
    deckard: Any
    kambi_session_id: str
    pam_user_id: str
    user_guid: str

    def get_events(self, sport: str) -> list:  # type: ignore[override]
        """Retrieve events for the given sport."""
        ...

    def log_back_in(self) -> None:
        """Re-authenticate the user session."""
        ...

    def deposit_funds(self, data: dict, wait: int) -> None:
        """Deposit funds into the user wallet."""
        ...

    @task
    def place_bet(self):
        betslip = data["betslip"]  # ty:ignore[unresolved-reference]

        for bet in betslip:
            if bet["type"] == "straight":
                events = self.get_events(bet["sport"])

                retries = 0
                while retries <= 2:
                    retries += 1
                    event = random.choice(events)
                    self.event_ids.append(event["event"]["id"])

                    if event["event"]["state"] == "STARTED":
                        with self.client.get(
                            f"/offerings/grouped_event/{event['event']['id']}/live_event/?market=US&lang=en_US",
                            headers={
                                "X-Corr-Id": self.user_correlation_id,
                                "X-Offering-Id": KAMBI_OFFERING_ID,
                            },
                            name="/offerings/grouped_event/live",
                            catch_response=True,
                        ) as response:
                            if response.status_code == 401:
                                self.log_back_in()

                    else:
                        with self.client.get(
                            f"/offerings/grouped_event/{event['event']['id']}/pre_match_event/?market=US&lang=en_US",
                            headers={
                                "X-Corr-Id": self.user_correlation_id,
                                "X-Offering-Id": KAMBI_OFFERING_ID,
                            },
                            name="/offerings/grouped_event/pre_match",
                            catch_response=True,
                        ) as response:
                            if response.status_code == 401:
                                self.log_back_in()

                outcomes = [
                    item
                    for sublist in [bo["outcomes"] for bo in event["betOffers"]]
                    for item in sublist
                ]
                outcomes = [
                    item
                    for item in outcomes
                    if item["status"] == "OPEN"
                    and item["odds"] < 21000
                    and item["odds"] > 1050
                ]
                if len(outcomes) == 0:
                    return
                outcome = random.choice(outcomes)

                stake = bet["wager"]

                if stake > self.wallet:
                    self.deposit_funds({"amount": self.deckard.wager("straight")}, 5)

                data = {
                    "couponRows": [
                        {
                            "index": 0,
                            "odds": outcome["odds"],
                            "outcomeId": outcome["id"],
                            "type": "SIMPLE",
                        }
                    ],
                    "bets": [
                        {"couponRowIndexes": [0], "eachWay": "false", "stake": stake}
                    ],
                    "allowedOddsChange": "NO",
                    "channel": "WEB",
                    "trackingData": {
                        "hasTeaser": False,
                        "isBetBuilderCombination": False,
                        "selectedOutcomes": [
                            {
                                "id": outcome["id"],
                                "outcomeId": outcome["id"],
                                "betofferId": outcome["betOfferId"],
                                "eventId": event["event"]["id"],
                                "approvedOdds": outcome["odds"],
                                "isLiveBetOffer": False,
                                "isPrematchBetoffer": True,
                                "fromBetBuilder": False,
                                "oddsApproved": True,
                                "eachWayApproved": True,
                                "source": "Event List View",
                            }
                        ],
                    },
                    "requestId": str(uuid.uuid4()),
                }

                LOGGER.info(json.dumps(data))

                with self.client.post(
                    f"{KAMBI_PLAYER_URL}/player/api/v2019/{KAMBI_OFFERING_ID}/coupon.json?lang=en_US&market=US&channel_id=1",
                    name=f"<kambi>/player/api/v2019/{KAMBI_OFFERING_ID}/coupon",
                    catch_response=True,
                    headers={
                        "Authorization": f"Bearer {self.kambi_session_id}",
                        "X-Corr-Id": self.user_correlation_id,
                        "Content-Type": "application/json",
                    },
                    data=json.dumps(data),
                ) as response:
                    if response.status_code == 401:
                        response.success()
                        self.log_back_in()

                    # TODO use this
                    if response.status_code == 200:
                        self.wallet -= stake

                    if response.status_code == 409 or response.status_code == 404:
                        LOGGER.error(
                            f"Kambi Coupon.json API returned {response.status_code}",
                            extra={
                                "kambi_wh_id": self.pam_user_id,
                                "piv_id": self.user_guid,
                                "outcome": outcome["id"],
                                "payload": data,
                                "event": event["event"]["id"],
                                "odds": outcome["odds"],
                                "response": response.content.decode(),
                                "wallet": self.wallet,
                                "stake": stake,
                            },
                        )
                        response.success()
