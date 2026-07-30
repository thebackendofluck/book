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
Synthetic User Engine (Replicant)

Generates realistic user behavior sequences for load testing a sports betting
platform. Each synthetic user ("replicant") has:

  - A complete fake identity (name, address, DOB, SSN stub)
  - A behavioral schema defining action probabilities and transitions
  - Exponentially-distributed pause times for realistic request spacing
  - Wager generation using normal distributions (per straight / parlay profiles)

The engine uses Markov-chain-like probabilistic transitions to decide
which action the user takes next (place_bet -> logout, place_bet -> deposit,
etc.), producing action sequences that mirror real bettor sessions.

Derived from a production load-testing codebase for a US-regulated
sportsbook platform.
"""

import datetime
import os
import random
import string
import uuid

import numpy as np
from faker import Faker  # ty:ignore[unresolved-import]
from faker.providers import internet  # ty:ignore[unresolved-import]

fake = Faker()
fake.add_provider(internet)


def next_step(current_step):
    """Pick the next action from a probability distribution."""
    return np.random.choice(
        list(current_step.keys()), 1, p=list(current_step.values())
    )[0]


class Replicant:
    """
    A synthetic user with an identity, behavioral profile, and a generated
    sequence of actions ("false memories") ready for execution by Locust.
    """

    def __init__(self, definition):
        self.registered = False
        self.finished = False
        self.false_memories = []
        self.ocular_serial_number = str(uuid.uuid4())  # correlation ID
        self.definition = definition["events"]
        self.password = os.getenv("LOAD_TEST_PASSWORD", "changeme")
        self.bettor_type = definition["type"]

        self.username = "".join(random.choice(string.ascii_lowercase) for _ in range(16))

        # KYC-compliant synthetic identity
        self.user_data = {
            "username": self.username,
            "email": (
                f"{fake.first_name()}.{fake.last_name()}"
                f".{datetime.datetime.utcnow().timestamp()}"  # ty:ignore[deprecated]
                f"@{fake.free_email_domain()}"
            ),
            "password": self.password,
            "confirm_password": self.password,
            "dob": str(fake.date_of_birth(minimum_age=21)),
            "first_name": f"{fake.first_name()}{self.username[:8]}",
            "last_name": f"{fake.last_name()}{self.username[9:]}",
            "address1": f"kyc_pass_4 secondary_check_pass {fake.street_address()}",
            "address2": fake.building_number(),
            "city": fake.city(),
            "state": "PA",
            "zipcode": fake.postcode_in_state("PA"),
            "phone": (
                f"{random.randint(100, 999)}-"
                f"{random.randint(100, 999)}-"
                f"{random.randint(100, 999)}"
            ),
            "channel": "WEB",
            "currency_code": "USD",
            "country_code": "1",
            "ssn_match": "1233",
            "confirm_info": True,
            "confirm_tos": True,
            "confirm_age": True,
        }

    # ----------------------------------------------------------------
    # Pause generation — exponential distribution capped at maxPause
    # ----------------------------------------------------------------
    def generate_pause(self, step, max_pause=600):
        avg = self.definition[step]["profile"].get("average_wait", 0.01)
        return min(np.random.exponential(avg), max_pause)

    # ----------------------------------------------------------------
    # Wager generation — normal distribution (never negative)
    # ----------------------------------------------------------------
    def wager(self, bet_type):
        profile = self.definition["place_bet"]["profile"][bet_type]
        return int(max(
            np.random.normal(profile["average_wager"], profile["variance_wager"]),
            0,
        ))

    def straight_bet(self):
        profile = self.definition["place_bet"]["profile"]
        sports = profile["sports"]
        bet_types = profile["bet_types"]

        return {
            "type": "straight",
            "odds": int(5 * round(
                np.random.normal(
                    profile["straight"]["average_odds"],
                    profile["straight"]["variance_odds"],
                ) / 5
            )),
            "sport": np.random.choice(
                list(sports.keys()), 1, p=list(sports.values())
            )[0],
            "bet_type": np.random.choice(
                list(bet_types.keys()), 1, p=list(bet_types.values())
            )[0],
            "wager": self.wager("straight"),
        }

    def parlay(self):
        profile = self.definition["place_bet"]["profile"]["parlay"]
        size = np.random.binomial(profile["total_legs"], profile["probability"], 1)[0]
        legs = [self.straight_bet() for _ in range(size)]
        return {
            "type": "parlay",
            "legs": [
                {k: v for k, v in leg.items() if k in ["odds", "sport", "bet_type"]}
                for leg in legs
            ],
            "wager": self.wager("parlay"),
        }

    # ----------------------------------------------------------------
    # Action generators — each appends to false_memories
    # ----------------------------------------------------------------
    def register(self):
        self.current_step = "register"
        self.finished = False
        self.false_memories.append({
            "action": self.current_step,
            "wait": self.generate_pause(self.current_step),
            "data": self.user_data,
        })

    def login(self):
        self.current_step = "login"
        self.finished = False
        self.false_memories.append({
            "action": self.current_step,
            "wait": self.generate_pause(self.current_step),
            "data": {k: v for k, v in self.user_data.items() if k in ["username", "password"]},
        })
        self.get_balance()

    def get_balance(self):
        self.false_memories.append({"action": "get_balance", "wait": 0, "data": None})

    def extend_session(self):
        self.false_memories.append({"action": "extend_session", "wait": 0, "data": None})

    def deposit_funds(self):
        self.current_step = "deposit_funds"
        self.finished = False
        self.false_memories.append({
            "action": self.current_step,
            "wait": self.generate_pause(self.current_step),
            "data": {"amount": 1_000_000},
        })
        self.get_balance()
        self.extend_session()

    def place_bet(self):
        self.current_step = "place_bet"
        self.finished = False
        profile = self.definition[self.current_step]["profile"]
        n_bets = int(np.random.uniform(1, 2 * profile["betslip"]["average_size"]))
        n_parlays = int(np.random.binomial(n_bets, profile["parlay"]["rate"], 1)[0])
        n_straights = n_bets - n_parlays
        bets = [self.parlay() for _ in range(n_parlays)]
        bets += [self.straight_bet() for _ in range(n_straights)]

        self.false_memories.append({
            "action": self.current_step,
            "wait": self.generate_pause(self.current_step),
            "data": {
                "number_bets": n_bets,
                "number_parlays": n_parlays,
                "number_straights": n_straights,
                "betslip": bets,
            },
        })
        self.get_balance()
        self.extend_session()

    def logout(self):
        self.current_step = "logout"
        self.finished = True
        self.false_memories.append({
            "action": self.current_step,
            "wait": self.generate_pause(self.current_step),
            "data": None,
        })

    # ----------------------------------------------------------------
    # Memory implantation — Markov walk through the schema
    # ----------------------------------------------------------------
    def implant_memories(self):
        """Build the full action sequence by walking the transition graph."""
        self.register()
        self.login()

        mapping = {
            "login": self.login,
            "get_balance": self.get_balance,
            "deposit_funds": self.deposit_funds,
            "place_bet": self.place_bet,
            "extend_session": self.extend_session,
            "logout": self.logout,
        }

        while not self.finished:
            transitions = self.definition[self.current_step]["transitions"]
            nxt = next_step(transitions)
            mapping[nxt]()


if __name__ == "__main__":
    import sys
    import yaml
    from pprint import pprint

    with open(f"../schemas/{sys.argv[1]}.yaml", "r") as f:
        definition = yaml.load(f, Loader=yaml.FullLoader)

    replicant = Replicant(definition)
    replicant.implant_memories()
    pprint(replicant.false_memories)
