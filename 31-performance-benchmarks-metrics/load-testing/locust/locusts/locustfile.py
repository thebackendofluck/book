# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import json
import logging
from locust_logger import LOGGER
import os
import random
import time
import uuid
import yaml
import string
import datetime

from locust import events
from locust import HttpUser, TaskSet, SequentialTaskSet, task, between
from locust.contrib.fasthttp import FastHttpUser
from locust.exception import StopUser, InterruptTaskSet
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
import numpy as np

import loadsim
from loadkit import DynamoClient
from validator import ValidatorTasks
from real_dumb import RealDumbTasks

from bet_placer import Bettor

# sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)

# sentry_sdk.init(
#     dsn="https://examplePublicKey@o0.ingest.sentry.io/0",
#     integrations=[sentry_logging],
#     environment=os.getenv("ENV", "staging"),
# )


LOG_DIR = os.getenv("LOG_DIR", "./")
BETTING_PROVIDER_OFFERING_URL = os.getenv(
    "BETTING_PROVIDER_OFFERING_URL", "https://acmetocasino-api.teste.workers.dev/bettingprovider/offering"
)
BETTING_PROVIDER_PLAYER_URL = os.getenv(
    "BETTING_PROVIDER_PLAYER_URL", "https://acmetocasino-api.teste.workers.dev/bettingprovider/auth"
)
BETTING_PROVIDER_OFFERING_ID = os.getenv("BETTING_PROVIDER_OFFERING_ID", "acmetocasino")
ACME_BASE_URL = os.getenv(
    "ACME_BASE_URL", "https://acmetocasino-api.teste.workers.dev"
)

USE_PREREG = os.getenv("USE_PREREG", "false")

users_list=[]
bettors = {"aggressive":1}

bettor_profiles = {}
for bettor_type in list(bettors.keys()):
    with open(f"../schemas/{bettor_type}.yaml", "r") as f:
        bettor_profiles[bettor_type] = yaml.load(f, Loader=yaml.FullLoader)

if USE_PREREG == "true":
    print("loading USER.CSV")
    with open("users.csv") as f:
        users_list = list(set(f.read().splitlines()))


def random_pause(low, high):
    time.sleep(random.randrange(low, high))


failed_records = []
loadkit_client = DynamoClient()
responses = []
user_actions = None
corr_id = None


@events.request_failure.add_listener
def on_failure(request_type, name, response_time, response_length, exception):
    if hasattr(exception, "request") and hasattr(exception, "response"):
        request = exception.request
        response = exception.response
        LOGGER.error(
            "Request failed '{}' - status:%s, content:%s".format(name),
            exception.response.status_code,
            exception.response.content.decode(),
            extra={
                "response_time": response_time,
                "response_length": response_length,
                "request_type": request_type,
                "payload": exception.request.payload,
            },
        )
        failed_records.append(
            {
                "url": request.url,
                "payload": request.payload,
                "response_status": response.status_code,
                "response_content": response.content.decode()
                if response.content
                else "",
            }
        )
    else:
        raise exception


@events.report_to_master.add_listener
def report_failures(client_id, data, **kw):
    data["failed_record"] = failed_records


master_fails = {}


@events.worker_report.add_listener
def aggregate_failures(client_id, data, **kw):
    master_fails[client_id] = data["failed_record"]


@events.test_stop.add_listener
@events.quitting.add_listener
def write_failures_to_file(**kw):
    with open(f"{LOG_DIR}/locust_failed_requests.csv", "w") as failed_report:
        for client_id in master_fails:
            for record in master_fails[client_id]:
                failed_report.write("REQUEST URL: {}\n".format(record["url"]))
                failed_report.write("REQUEST PAYLOAD: {}\n".format(record["payload"]))
                failed_report.write(
                    "RESPONSE STATUS: {}\n".format(record["response_status"])
                )
                failed_report.write(
                    "RESPONSE CONTENT: {}\n\n".format(record["response_content"])
                )

    if all([x is not None for x in (corr_id, user_actions, responses)]):
        loadkit_client.put_runs(corr_id, user_actions, responses)


class RoboTasks(TaskSet):
    wait_time = between(0.0, 0.0)
    wallet = 0.0
    event_ids = []
    login_count = 0

    def on_start(self):
        self.map_functions()

        bettor_type = np.random.choice(
            list(bettors.keys()), 1, p=list(bettors.values())
        )[0]

        schema = bettor_profiles[bettor_type]

        if len(users_list) > 0:
            prereg = bool(np.random.binomial(1, 0.95))
            # prereg=True

        self.deckard = loadsim.Replicant(schema)
        self.deckard.implant_memories()
        self.user_actions = self.deckard.false_memories
        self.user_correlation_id = self.deckard.ocular_serial_number
        global corr_id
        corr_id = self.user_correlation_id
        global user_actions
        user_actions = self.user_actions


        if len(users_list) > 0 and prereg:
            random.shuffle(users_list)
            self.username = users_list.pop()
            self.prereg = True
            self.password = os.getenv("LOADTEST_PASSWORD", "loadtest-user")
            self.tasks.append(self.login(None, 0))
        else:
            self.tasks.append(self.register(self.deckard.user_data, 0))
            self.prereg = False

        tasks = []
        for memory in self.deckard.false_memories[1:]:
            tasks.append(self._map[memory["action"]](memory["data"], memory["wait"]))

        raise StopUser

    @task
    def register(self, data, wait):
        self.username = data["username"]
        self.password = data["password"]

        with self.client.post(
            "/users/register/",
            headers={"X-Corr-Id": self.user_correlation_id, "Consumer": "script"},
            data=data,
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                LOGGER.error(
                    f"Registration returned {response.status_code}",
                    extra={
                        "username": self.username,
                        "response": response.content.decode(),
                    },
                )
                response.failure(
                    f"user failed to register {self.username}: {response.content.decode()}"
                )
                raise InterruptTaskSet
                raise StopUser
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
            resp_data = response.json()["data"]
            responses.append(res)
            self.bettingprovider_session_id = resp_data.get("bettingprovider_session_id")
            self.bettingprovider_routing_key = resp_data.get("bettingprovider_routing_key")
            self.pam_session_id = resp_data.get("pam_session_id")
            self.pam_user_id = resp_data.get("pam_user_id")
            self.token = resp_data.get("token")
            self.user_guid = resp_data.get("guid")
            self.kyc_approved = resp_data.get("kyc_approved")
            self.kyc_status = resp_data.get("kyc_status")
            self.geocomply_jwt = resp_data.get("geocomply_jwt")
            self.state_config_jwt = resp_data.get("state_config_jwt")


        time.sleep(wait)

    @task
    def login(self, _, wait):
        with self.client.post(
            "/users/login/",
            headers={"X-Corr-Id": self.user_correlation_id, "Consumer": "script"},
            data={"username": self.username, "password": self.password},
            name=f"/users/login/{self.login_count}",
            catch_response=True,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                LOGGER.error(
                    f"Login returned {response.status_code}: {response.text}",
                    extra={
                        "username": self.username,
                        # "bettingprovider_wh_id": self.pam_user_id,
                        # "piv_id": self.user_guid,
                        "response": response.content.decode(),
                    },
                )

                response.failure(f"user failed to login. error: {response.content.decode()}")

                raise InterruptTaskSet
                raise StopUser

            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }

            responses.append(res)

            response_data = response.json()["data"]

            self.token = response_data["token"]
            self.geocomply_jwt = response_data["geocomply_jwt"]
            self.bettingprovider_session_id = response_data["bettingprovider_session_id"]
            self.pam_session_id = response_data["pam_session_id"]
            self.pam_user_id = response_data["pam_user_id"]
            self.user_guid = response_data["guid"]
            self.kyc_approved = response_data["kyc_approved"]
            self.kyc_status = response_data["kyc_status"]

        time.sleep(wait)

    def log_back_in(self):
        #self.login_count += 1
        self.login(None, 1)
        self.get_profile(None, 0)
        self.get_config(None, 0)
        self.generate_geo_test_packet(None, 0)
        self.geo_validate(None, 0)
        self.get_homepage(None, 3)

    @task
    def get_profile(self, _, wait):
        with self.client.get(
            "/users/profile/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            response_data = response.json()
            responses.append(res)

        time.sleep(wait)

    @task
    def get_config(self, _, wait):
        with self.client.get(
            f"/main/config/{self.deckard.user_data['state']}",
            headers={
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
        time.sleep(wait)

    @task
    def get_homepage(self, _, wait):
        response = self.client.get(
            "/offerings/homepage",
            headers={
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
        ).json()["layout"]

        # arrange group ids for Kambi section/eventgroup call
        groupids = ",".join(str(item["groupId"]) for item in response)

        # arrange paths for Kambi /listView call
        paths = []
        level1 = ""
        level2 = ""
        level3 = ""
        level4 = ""
        for item in response:
            paths.append(item["path"])

        # add all to paths that do not have 4 levels of path data
        for i in range(len(paths)):
            while len(paths[i]) < 4:
                paths[i].append("all")

        # organize path data by levels
        for path in paths[:-1]:
            level1 += path[0] + ","
            level2 += path[1] + ","
            level3 += path[2] + ","
            level4 += path[3] + ","
        level1 += paths[-1][0]
        level2 += paths[-1][1]
        level3 += paths[-1][2]
        level4 += paths[-1][3]

        # call Kambi section/eventgroup with the group id's from layout of homepage
        self.client.get(
            f"{BETTING_PROVIDER_OFFERING_URL}/offering/v2018/{BETTING_PROVIDER_OFFERING_ID}/section/eventgroup/{groupids}.json"
            f"?lang=en_US&market=US&useCombined=true&onlyMatches=true",
            headers={"X-Corr-Id": self.user_correlation_id},
            name=f"Homepage<bettingprovider>/offerings/section/eventgroup",
        )

        # call Kambi ListView with organized path data
        response = self.client.get(
            f"{BETTING_PROVIDER_OFFERING_URL}/offering/v2018/{BETTING_PROVIDER_OFFERING_ID}/listView/"
            f"{level1}/{level2}/{level3}/{level4}/?include"
            f"Participants=true&useCombined=true&lang=en_US&market=US",
            headers={"X-Corr-Id": self.user_correlation_id},
            name=f"Homepage<bettingprovider>/offerings/ListView",
        ).json()["events"]

        # parse eventids from the listview response
        eventids = ",".join(str(event["event"]["id"]) for event in response)

        # call Kambi betoffer/event
        self.client.get(
            f"{BETTING_PROVIDER_OFFERING_URL}/offering/v2018/{BETTING_PROVIDER_OFFERING_ID}/betoffer/event/{eventids}.json?market"
            f"=US&lang=en_US",
            headers={"X-Corr-Id": self.user_correlation_id},
            name=f"Homepage<bettingprovider>/offerings/betoffer/event",
        )

        time.sleep(wait)

    @task
    def get_balance(self, _, wait):
        with self.client.post(
            f"{ACME_BASE_URL}/usergateway/getbalance",
            headers={
                "Consumer": "script",
                "Content-Type": "application/json",
            },
            name="/getbalance",
            data=json.dumps(
                {
                    "type": "getbalance",
                    "sessionid": self.pam_session_id,
                }
            ),
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
            # LOGGER.info(f"Wallet Balance: {response.json()}")
        time.sleep(wait)

    @task
    def generate_geo_test_packet(self, _, wait):
        with self.client.post(
            f"/geocomply/dev_generate/{self.deckard.user_data['state']}",
            headers={
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
                "X-Geo-JWT": self.geocomply_jwt,
            },
            data=json.dumps(
                {
                    "error_code": 0,
                    "state": self.deckard.user_data["state"],
                    "geolocate_in": 9000,
                    "troubleshooter": [],
                }
            ),
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }

            responses.append(res)

            if response.status_code >= 400:
                raise InterruptTaskSet
            else:
                self.geocomply_state = res["content"]["state"]
                self.geocomply_packet = res["content"]["packet"]

        time.sleep(wait)

    @task
    def geo_validate(self, _, wait):
        with self.client.post(
            "/geocomply/validate/",
            headers={
                "X-Geo-JWT": self.geocomply_jwt,
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
            data=json.dumps(
                {"state": self.geocomply_state, "packet": self.geocomply_packet}
            ),
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            if response.status_code < 200 or response.status_code >= 400:
                LOGGER.error(
                    f"geo_validate {response.status_code} for user {self.username}, attempting to log back in. \nMessage: {response.text}",
                    extra={
                        "username": self.username,
                        "response": response.content.decode(),
                    },
                )
                #response.success()
                self.log_back_in()

            responses.append(res)

        time.sleep(wait)

    @task
    def deposit_funds(self, data, wait):
        with self.client.post(
            "/users/deposit/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
            data=data,
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
            if response.status_code < 200 or response.status_code >= 400:
                LOGGER.error(
                    f"deposits code: {response.status_code} for user: {self.username}. \npayload: {data}",
                )    
        # LOGGER.info(f"Successful deposit response:\n {response.json()}\n PAM User id: {self.pam_user_id}")
        self.wallet += data["amount"]
        time.sleep(wait)

    @task
    def place_bet(self, data, wait):
        n_bets = random.randint(2, 16)

        bettor = Bettor(
            client=self.client,
            bettingprovider_offering_url=BETTING_PROVIDER_OFFERING_URL,
            bettingprovider_player_api=BETTING_PROVIDER_PLAYER_URL,
            bettingprovider_offering_id=BETTING_PROVIDER_OFFERING_ID,
            bettingprovider_session_id=self.bettingprovider_session_id,
            correlation_id=self.user_correlation_id,
            user_id=self.user_guid,
            user=self.username,
            pam_user_id=self.pam_user_id,
        )
        response = bettor.create_betslip(
            sport="basketball", group=None, method="random", count=n_bets
        )

        if response == "USER_NOT_AUTHENTICATED":
            self.log_back_in()
            bettor.create_betslip(
                sport="basketball", group=None, method="random", count=n_bets
            )


        time.sleep(wait)

    @task
    def extend_session(self, _, wait):
        with self.client.post(
            "/users/extend_session/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            if response.status_code < 200 or response.status_code >= 300:
                LOGGER.error(
                    f"extend session returned {response.status_code}, attempting to log back in. \nMessage: {response.text}",
                    extra={
                        "username": self.username,
                        "response": response.content.decode(),
                    },
                )
                response.success()
                self.log_back_in()

            response_data = response.json()["data"]
            responses.append(response_data)

        time.sleep(wait)

    @task
    def get_coupon_history(self, _, wait):
        with self.client.get(
            f"{BETTING_PROVIDER_PLAYER_URL}/player/api/v2019/{BETTING_PROVIDER_OFFERING_ID}/coupon/history.json",
            headers={
                "Authorization": f"Bearer {self.bettingprovider_session_id}",
                "X-Corr-Id": self.user_correlation_id,
            },
            name="/coupon/history/",
            catch_response=True,
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
            if response.status_code == 401:
                self.log_back_in()
                response.success()
        time.sleep(wait)

    @task
    def logout(self, _, wait):
        with self.client.post(
            "/users/logout/",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Corr-Id": self.user_correlation_id,
                "Consumer": "script",
            },
        ) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
        time.sleep(wait)

    @task
    def fetch_temp_homepage(self, _, wait):
        temp_url = 'https://platform.staging.acmetocasino.com/dummy.html'
        with self.client.get(temp_url) as response:
            res = {
                "status_code": response.status_code,
                "response": response.content.decode(),
                "content": response.json(),
            }
            responses.append(res)
        time.sleep(wait)

    def on_stop(self):

        LOGGER.info("Stopping bish")
        self.client.put_runs(
            self.user_correlation_id, self.deckard.false_memories[1:], self.user_actions
        )

    def map_functions(self):
        self._map = {
            "register": self.register,
            "login": self.login,
            "fetch_temp_homepage": self.fetch_temp_homepage,
            "get_profile": self.get_profile,
            "get_config": self.get_config,
            "get_balance": self.get_balance,
            "get_homepage": self.get_homepage,
            "geo_validate": self.geo_validate,
            "generate_geo_test_packet": self.generate_geo_test_packet,
            "deposit_funds": self.deposit_funds,
            "place_bet": self.place_bet,
            "get_coupon_history": self.get_coupon_history,
            "extend_session": self.extend_session,
            "logout": self.logout,
            "coupon_history": self.get_coupon_history,
        }


class RobotSalami(FastHttpUser):
    tasks = [RoboTasks]
    wait_time = between(0, 0)


#class Validator(FastHttpUser):
#    tasks = [ValidatorTasks]
#    wait_time = between(0, 0)


#class RealDumb(FastHttpUser):
#    tasks = [RealDumbTasks]
#    wait_time = between(0, 0)