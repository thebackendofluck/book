# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import os
import string
from locust import TaskSet, task
import random
from faker import Faker
from faker.providers import internet
import numpy as np
import datetime
import uuid

fake = Faker()
fake.add_provider(internet)


def next_step(current_step):
    try:
        return np.random.choice(
            list(current_step.keys()), 1, p=list(current_step.values())
        )[0]
    except Exception as e:
        print(e)
        print(current_step)


class Replicant:
    def __init__(self, definition):
        self.registered = False
        self.finished = False
        self.false_memories = []
        self.ocular_serial_number = str(uuid.uuid4())
        self.definition = definition["events"]
        self.password = os.getenv("LOADTEST_PASSWORD", "loadtest-user")
        self.bettor_type = definition["type"]
        self.username = "".join(
            [random.choice(string.ascii_lowercase) for i in range(16)]
        )
        self.user_data = {
            "username": self.username,
            "email": f"{fake.first_name()}.{fake.last_name()}.{datetime.datetime.now(datetime.timezone.utc).timestamp()}@{fake.free_email_domain()}",
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
            "phone": f"{random.randint(100, 999)}-{random.randint(100, 999)}-{random.randint(100, 999)}",
            "first_security_question_guid": "988c5bdb-698c-4d47-9f0a-c5db886ef502",
            "first_security_question_answer": "drodaisgod",
            "second_security_question_guid": "8794afbe-d9da-4d71-8374-baedffc23e36",
            "second_security_question_answer": "addyistheman",
            "preferences": {
                "odds_format": "american",  # @TODO make this random
                "require_login_security_questions": False,  # @TODO can make random? random_bool(.3),
                "login_notifications": False,
                "review_line_change": False,
            },
            "channel": "WEB",
            "currency_code": "USD",
            "country_code": "1",
            "regulation_id": "US_WEST_VIRGINIA",
            "ssn_match": "1233",
            "confirm_info": True,
            "confirm_tos": True,
            "confirm_age": True,
            "confirm_keycasino": True,
            "confirm_crimescode": True,
        }
        self.event_stream = []

    def generate_pause(self, step, maxPause=600):
        if "average_wait" in self.definition[step]["profile"]:
            average_wait = self.definition[step]["profile"]["average_wait"]
        else:
            average_wait = 0.01

        return min(np.random.exponential(average_wait), maxPause)

    def wager(self, bet_type):
        profile = self.definition["place_bet"]["profile"][bet_type]
        return int(
            max(
                np.random.normal(profile["average_wager"], profile["variance_wager"]), 0
            )
        )

    def straight_bet(self):
        profile = self.definition["place_bet"]["profile"]
        straights = profile["straight"]
        sports = profile["sports"]
        types = profile["bet_types"]

        return {
            "type": "straight",
            # round to nearest 5
            "odds": int(
                5
                * round(
                    np.random.normal(
                        straights["average_odds"], straights["variance_odds"]
                    )
                    / 5
                )
            ),
            "sport": np.random.choice(list(sports.keys()), 1, p=list(sports.values()))[
                0
            ],
            "bet_type": np.random.choice(list(types.keys()), 1, p=list(types.values()))[
                0
            ],
            "wager": self.wager("straight"),
        }

    def parlay(self):
        profile = self.definition["place_bet"]["profile"]["parlay"]
        size = np.random.binomial(profile["total_legs"], profile["probability"], 1)[0]

        legs = []
        for leg in range(size):
            legs.append(self.straight_bet())

        return {
            "type": "parlay",
            "legs": [
                {k: v for k, v in leg.items() if k in ["odds", "sport", "bet_type"]}
                for leg in legs
            ],
            "wager": self.wager("parlay"),
        }

    def register(self):
        self.current_step = "register"
        self.finished = False
        self.false_memories.append(
            {
                "action": self.current_step,
                "url": self.definition[self.current_step]["profile"]["url"],
                "wait": self.generate_pause(self.current_step),
                "data": self.user_data,
            }
        )

    def login(self):
        self.current_step = "login"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "url": self.definition[self.current_step]["profile"]["url"],
                "wait": self.generate_pause(self.current_step),
                "data": {
                    k: v
                    for k, v in self.user_data.items()
                    if k in ["username", "password"]
                },
            }
        )

        self.get_balance()

    def get_profile(self):
        self.current_step = "get_profile"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "url": self.definition[self.current_step]["profile"]["url"],
                "data": None,
            }
        )

        self.get_balance()

    def get_config(self):
        self.current_step = "get_config"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "url": self.definition[self.current_step]["profile"]["url"]
                + self.user_data["state"],
                "data": None,
            }
        )

    def generate_geo_test_packet(self):
        self.current_step = "generate_geo_test_packet"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "url": self.definition[self.current_step]["profile"]["url"]
                + self.user_data["state"],
                "data": None,
            }
        )

    def geo_validate(self):
        self.current_step = "geo_validate"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "url": self.definition[self.current_step]["profile"]["url"],
                "data": None,
            }
        )

    def get_homepage(self):
        self.current_step = "get_homepage"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "url": self.definition[self.current_step]["profile"]["url"],
                "data": None,
            }
        )

        self.get_balance()
        self.extend_session()

    def get_balance(self):
        self.finished = False

        self.false_memories.append(
            {
                "action": "get_balance",
                "wait": 0,
                "data": None,
            }
        )


    def fetch_temp_homepage(self):
        self.finished = False

        self.false_memories.append(
            {
                "action": "fetch_temp_homepage",
                "wait": 0,
                "data": None,
            }
        )

    def deposit_funds(self):
        self.current_step = "deposit_funds"
        self.finished = False

        average = self.definition[self.current_step]["profile"]["average"]
        variance = self.definition[self.current_step]["profile"]["variance"]

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "url": self.definition[self.current_step]["profile"]["url"],
                "data": {"amount": 1000000},
            }
        )
        self.get_balance()
        self.extend_session()

    def place_bet(self):
        self.current_step = "place_bet"
        self.finished = False

        betting_profile = self.definition[self.current_step]["profile"]

        number_bets = int(
            np.random.uniform(1, 2 * betting_profile["betslip"]["average_size"])
        )
        number_parlays = int(
            np.random.binomial(number_bets, betting_profile["parlay"]["rate"], 1)[0]
        )
        number_straights = number_bets - number_parlays

        bets = []
        for i in range(number_parlays):
            bets.append(self.parlay())

        for i in range(number_straights):
            bets.append(self.straight_bet())

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "data": {
                    "number_bets": number_bets,
                    "number_parlays": number_parlays,
                    "number_straights": number_straights,
                    "betslip": bets,
                },
            }
        )
        self.get_balance()
        self.extend_session()

    def coupon_history(self):
        self.current_step = "coupon_history"
        self.finished = False

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "data": None,
            }
        )
        self.get_balance()
        self.extend_session()

    def extend_session(self):
        self.finished = False

        self.false_memories.append(
            {
                "action": "extend_session",
                "wait": 0,
                "data": None,
            }
        )
        
    def logout(self):
        self.current_step = "logout"
        self.finished = True

        self.false_memories.append(
            {
                "action": self.current_step,
                "wait": self.generate_pause(self.current_step),
                "data": None,
            }
        )

    def implant_memories(self): 
        self.register()
        self.login()

        mapping = {
            "login": self.login,
            "get_profile": self.get_profile,
            "get_config": self.get_config,
            "fetch_temp_homepage": self.fetch_temp_homepage,
            "generate_geo_test_packet": self.generate_geo_test_packet,
            "geo_validate": self.geo_validate,
            "homepage_landing": self.get_homepage,
            "get_homepage": self.get_homepage,
            "get_balance": self.get_balance,
            "deposit_funds": self.deposit_funds,
            "place_bet": self.place_bet,
            "coupon_history": self.coupon_history,
            "extend_session": self.extend_session,
            "logout": self.logout,
        }

        while not self.finished:
            transitions = self.definition[self.current_step]["transitions"]
            next_event = next_step(transitions)

            mapping[next_event]()


if __name__ == "__main__":
    import yaml, sys
    from pprint import pprint

    with open(f"../schemas/{sys.argv[1]}.yaml", "r") as f:
        definition = yaml.load(f, Loader=yaml.FullLoader)

    deckard = Replicant(definition)
    deckard.implant_memories()
    # deckard.schedule_reboots()
    pprint(deckard.false_memories)
