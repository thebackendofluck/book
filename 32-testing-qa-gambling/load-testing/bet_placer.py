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

from types import SimpleNamespace
from typing import Any
import json
import random
import requests
import uuid
from locust_logger import LOGGER


class Betslip:
    def __init__(self, outcomes):

        #         "betofferId": outcome["betOfferId"],
        # "eventId": event["event"]["id"],
        # "approvedOdds": outcome["odds"],
        # "isLiveBetOffer": False,
        # "isPrematchBetoffer": True,
        # "fromBetBuilder": False,

        # "eachWayApproved": True,
        # "source": "Event List View",
        outcomes = [(index, outcome) for index, outcome in enumerate(outcomes)]
        coupon_row_indexes = [outcome[0] for outcome in outcomes]
        coupon_rows = [
            {
                "index": outcome[0],
                "odds": outcome[1].odds,
                "outcomeId": outcome[1].id,
                "type": "SIMPLE",
            }
            for outcome in outcomes
        ]

        self.betslip = {
            "couponRows": coupon_rows,
            "bets": [
                {
                    "couponRowIndexes": coupon_row_indexes,
                    "eachWay": "false",
                    "stake": 50,
                }
            ],
            "allowedOddsChange": "NO",
            "channel": "WEB",
            "trackingData": {
                "hasTeaser": False,
                "isBetBuilderCombination": False,
                "selectedOutcomes": [
                    {
                        "id": outcome["id"],  # ty:ignore[unresolved-reference]
                        "outcomeId": outcome["id"],  # ty:ignore[unresolved-reference]
                        "betofferId": outcome["betOfferId"],  # ty:ignore[unresolved-reference]
                        "eventId": event["event"]["id"],  # ty:ignore[unresolved-reference]
                        "approvedOdds": outcome["odds"],  # ty:ignore[unresolved-reference]
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


class Bettor:
    client: Any
    kambi_offering_url: str
    kambi_player_api: str
    kambi_offering_id: str
    correlation_id: str
    kambi_session_id: str
    pam_user_id: str

    def __init__(self, **kwargs: Any):
        self.user: str = kwargs.get("user", "")
        self.user_id: str = kwargs.get("user_id", "")
        self.client = kwargs["client"]
        self.kambi_offering_url = kwargs.get("kambi_offering_url", "")
        self.kambi_player_api = kwargs.get("kambi_player_api", "")
        self.kambi_offering_id = kwargs.get("kambi_offering_id", "")
        self.correlation_id = kwargs.get("correlation_id", "")
        self.kambi_session_id = kwargs.get("kambi_session_id", "")
        self.pam_user_id = kwargs.get("pam_user_id", "")
        self.browser_headers = {
            "authority": kwargs.get("kambi_player_api"),
            "pragma": "no-cache",
            "cache-control": "no-cache",
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.192 Safari/537.36",
            "content-type": "application/json;charset=UTF-8",
            "origin": "http://localhost:8080",
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": "http://localhost:8080/",
            "accept-language": "en-US,en;q=0.9",
        }
        self.bet_offers = []

    def find_game(self, sport, group):
        url = f"{self.kambi_offering_url}/offering/v2018/{self.kambi_offering_id}/listView/{sport}/all/all/all/matches.json?includeParticipants=true&useCombined=true&lang=en_US&market=US"
        headers = {"X-Corr-Id": self.correlation_id}

        response = self.client.get(
            url, headers=headers, name=f"<kambi>/offerings/{sport}"
        )

        events = [
            event
            for event in response.json()["events"]
            if event["event"]["state"] in ["STARTED", "NOT_STARTED"]
        ]

        if group is not None:
            events = [
                event
                for event in events
                if event.get("event", {}).get("group") == group
            ]

        return SimpleNamespace(**random.choice(events))

    def pick_bet(self, sport, group, **kwargs):
        method = kwargs.get("method")
        criterion = kwargs.get("criterion", "Moneyline")

        game = self.find_game(sport, group)

        self.criterion = {
            "Point Spread": "point_spread",
            "Moneyline": "moneyline",
            "Total Points": "total_points",
        }[criterion]

        bet_offer = SimpleNamespace(**random.choice([g for g in game.betOffers]))

        outcome = random.choice(bet_offer.outcomes)

        self.bet_offers.append(
            {
                "event_id": bet_offer.eventId,
                "bet_offer_id": bet_offer.id,
                "criterion_id": bet_offer.criterion.get("id"),
                "criterion_label": bet_offer.criterion.get("label"),
                "bet_offer_type_id": bet_offer.betOfferType.get("id"),
                "bet_offer_type_name": bet_offer.betOfferType.get("name"),
                "outcome_id": outcome.get("id"),
                "outcome_label": outcome.get("label"),
                "outcome_type": outcome.get("type"),
                "odds": outcome.get("odds"),
                "participant_id": outcome.get("participantId"),
                "outcome_status": outcome.get("status"),
            }
        )

        if len(self.bet_offers) >= 2:
            self.validate_betslip()

    def create_betslip(self, sport, group, count, **kwargs):
        for i in range(count):
            self.pick_bet(sport, group)

        self.place_bet()

    def validate_betslip(self):
        if len(self.bet_offers) < 2:
            self.betslip_valid = True

        outcomes = [
            (index, bet_offer) for index, bet_offer in enumerate(self.bet_offers)
        ]
        indexes = [out[0] for out in outcomes]

        coupon_rows = [
            {
                "index": out[0],
                "odds": out[1].get("odds"),
                "outcomeId": out[1].get("outcome_id"),
                "type": "SIMPLE",
            }
            for out in outcomes
        ]

        request = {
            "bets": [{"couponRowIndexes": indexes, "eachWay": False}],
            "couponRows": coupon_rows,
            "allowOddsChange": "NO",
        }

        with self.client.post(
            f"{self.kambi_player_api}/player/api/v2019/{self.kambi_offering_id}/coupon/validate.json?lang=en_US&market=US",
            headers=self.browser_headers,
            data=json.dumps(request),
            name="<kambi>/coupon/validate",
            catch_response=True,
        ) as response:
            #LOGGER.info(f"response from validation for betslip: {response.json()}")
            if response.status_code in [400, 409]:
                self.bet_offers.pop(-1)
                response.success()

    def place_bet(self):
        bets = [(index, bet_offer) for index, bet_offer in enumerate(self.bet_offers)]

        coupon_rows = [
            {
                "index": bet[0],
                "odds": bet[1].get("odds"),
                "outcomeId": bet[1].get("outcome_id"),
                "type": "SIMPLE",
            }
            for bet in bets
        ]

        b = [
            {
                "couponRowIndexes": [bet[0]],
                "eachWay": False,
                "stake": 5000,
                "payload": bet[0],
            }
            for bet in bets
        ]

        data = {
            "couponRows": coupon_rows,
            "bets": b,
            "allowOddsChange": "HIGHER",
            "requestId": str(uuid.uuid4()),
        }

        #data = {
        #    "bets": [
        #        {"couponRowIndexes": [0], "eachWay": False, "stake": 5000, "payload": 0}
        #    ],
        #    "couponRows": [
        #        {"index": 0, "odds": 1880, "outcomeId": 2670367092, "type": "SIMPLE"}
        #    ],
        #    "requestId": "e8d8b777-1dba-4d0e-9687-2253dbce2b17",
        #    "allowOddsChange": "HIGHER",
        #}

        with self.client.post(
            f"{self.kambi_player_api}/player/api/v2019/{self.kambi_offering_id}/coupon.json?lang=en_US&market=US",
            headers={
                "Authorization": f"Bearer {self.kambi_session_id}",
                "X-Corr-Id": self.correlation_id,
                "Content-Type": "application/json",
                "authority": "auth.example.com",
                "origin": "https://sb-drill.newacme.io",
                # "referer": "https://sb-drill.newacme.io/",
                "sec-ch-ua": '"Google Chrome";v="89", "Chromium";v="89", ";Not A Brand";v="99"',
                "accept": "application/json, text/plain, */*",
                "sec-ch-ua-mobile": "?0",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 11_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36",
                "content-type": "application/json;charset=UTF-8",
                "sec-fetch-site": "cross-site",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            },
            data=json.dumps(data),
            name="<kambi>/coupon/",
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                response.failure(
                    f"Error: {response.status_code}\n Message: {response.text}\n User: {self.user}\n GUID: {self.user_id}\n PAM User ID: {self.pam_user_id} \n Payload: {json.dumps(data)}"
                )
