# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Betting API Integration for Load Testing

Handles the complete bet lifecycle against a sportsbook provider's Offering
and Player APIs:

  1. find_game  — query /listView for available events in a sport
  2. pick_bet   — select a random bet offer and outcome
  3. validate   — POST to /coupon/validate to check the betslip
  4. place_bet  — POST to /coupon to submit the wager

Production patterns:
  - Browser-realistic HTTP headers (User-Agent, sec-fetch-*, CORS)
  - Automatic betslip cleanup on 400/409 validation responses
  - Configurable offering ID for multi-brand testing
  - Session-based authentication via Bearer tokens

Derived from a production load-testing codebase. Provider-specific URLs
and credentials have been replaced with configurable parameters.
"""

import json
import random
import uuid
from types import SimpleNamespace


class Bettor:
    """
    Encapsulates the full betting flow for a single synthetic user session.
    Requires a Locust HTTP client and sportsbook API configuration.
    """

    def __init__(self, **kwargs):
        self.user = kwargs.get("user")
        self.user_id = kwargs.get("user_id")
        self.client = kwargs.get("client")
        self.offering_url = kwargs.get("offering_url")
        self.player_api = kwargs.get("player_api")
        self.offering_id = kwargs.get("offering_id")
        self.correlation_id = kwargs.get("correlation_id")
        self.session_id = kwargs.get("session_id")
        self.pam_user_id = kwargs.get("pam_user_id")

        # Mimic real browser headers for realistic load patterns
        self.browser_headers = {
            "authority": self.player_api or "",
            "pragma": "no-cache",
            "cache-control": "no-cache",
            "accept": "application/json, text/plain, */*",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/88.0.4324.192 Safari/537.36"
            ),
            "content-type": "application/json;charset=UTF-8",
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "accept-language": "en-US,en;q=0.9",
        }
        self.bet_offers = []

    # ----------------------------------------------------------------
    # Step 1: Find an available game for a given sport
    # ----------------------------------------------------------------
    def find_game(self, sport, group=None):
        url = (
            f"{self.offering_url}/offering/v2018/{self.offering_id}"
            f"/listView/{sport}/all/all/all/matches.json"
            f"?includeParticipants=true&useCombined=true&lang=en_US&market=US"
        )
        response = self.client.get(  # ty:ignore[unresolved-attribute]
            url,
            headers={"X-Corr-Id": self.correlation_id},
            name=f"<sportsbook>/offerings/{sport}",
        )
        events = [
            e for e in response.json()["events"]
            if e["event"]["state"] in ("STARTED", "NOT_STARTED")
        ]
        if group is not None:
            events = [e for e in events if e.get("event", {}).get("group") == group]

        return SimpleNamespace(**random.choice(events))

    # ----------------------------------------------------------------
    # Step 2: Pick a random bet offer + outcome from a game
    # ----------------------------------------------------------------
    def pick_bet(self, sport, group=None):
        game = self.find_game(sport, group)
        bet_offer = SimpleNamespace(**random.choice(game.betOffers))
        outcome = random.choice(bet_offer.outcomes)

        self.bet_offers.append({
            "event_id": bet_offer.eventId,
            "bet_offer_id": bet_offer.id,
            "criterion_id": bet_offer.criterion.get("id"),
            "criterion_label": bet_offer.criterion.get("label"),
            "outcome_id": outcome.get("id"),
            "outcome_label": outcome.get("label"),
            "odds": outcome.get("odds"),
            "outcome_status": outcome.get("status"),
        })

        if len(self.bet_offers) >= 2:
            self.validate_betslip()

    # ----------------------------------------------------------------
    # Step 3: Validate the betslip — remove invalid legs
    # ----------------------------------------------------------------
    def validate_betslip(self):
        if len(self.bet_offers) < 2:
            return

        outcomes = list(enumerate(self.bet_offers))
        coupon_rows = [
            {
                "index": idx,
                "odds": bo.get("odds"),
                "outcomeId": bo.get("outcome_id"),
                "type": "SIMPLE",
            }
            for idx, bo in outcomes
        ]
        request_body = {
            "bets": [{"couponRowIndexes": [idx for idx, _ in outcomes], "eachWay": False}],
            "couponRows": coupon_rows,
            "allowOddsChange": "NO",
        }

        with self.client.post(  # ty:ignore[unresolved-attribute]
            f"{self.player_api}/player/api/v2019/{self.offering_id}"
            f"/coupon/validate.json?lang=en_US&market=US",
            headers=self.browser_headers,
            data=json.dumps(request_body),
            name="<sportsbook>/coupon/validate",
            catch_response=True,
        ) as response:
            if response.status_code in (400, 409):
                # Invalid combination — drop the last added leg
                self.bet_offers.pop(-1)
                response.success()

    # ----------------------------------------------------------------
    # Step 4: Place the bet
    # ----------------------------------------------------------------
    def place_bet(self):
        bets = list(enumerate(self.bet_offers))
        coupon_rows = [
            {
                "index": idx,
                "odds": bo.get("odds"),
                "outcomeId": bo.get("outcome_id"),
                "type": "SIMPLE",
            }
            for idx, bo in bets
        ]
        bet_entries = [
            {
                "couponRowIndexes": [idx],
                "eachWay": False,
                "stake": 5000,
                "payload": idx,
            }
            for idx, _ in bets
        ]

        data = {
            "couponRows": coupon_rows,
            "bets": bet_entries,
            "allowOddsChange": "HIGHER",
            "requestId": str(uuid.uuid4()),
        }

        with self.client.post(  # ty:ignore[unresolved-attribute]
            f"{self.player_api}/player/api/v2019/{self.offering_id}"
            f"/coupon.json?lang=en_US&market=US",
            headers={
                "Authorization": f"Bearer {self.session_id}",
                "X-Corr-Id": self.correlation_id,
                "Content-Type": "application/json",
                "accept": "application/json, text/plain, */*",
            },
            data=json.dumps(data),
            name="<sportsbook>/coupon/",
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                response.failure(
                    f"Error: {response.status_code} | "
                    f"User: {self.user} | GUID: {self.user_id}"
                )

    # ----------------------------------------------------------------
    # Convenience: full flow — pick N bets then place
    # ----------------------------------------------------------------
    def create_betslip(self, sport, group=None, count=4):
        for _ in range(count):
            self.pick_bet(sport, group)
        self.place_bet()
