# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Composable Locust task set for platform API requests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid

from locust import TaskSet, task


class PlatformRequests(TaskSet):
    """Core platform API calls shared by every performance scenario."""

    hub_url = os.environ["HUB_URL"]

    @task
    def register(self) -> None:
        jurisdiction = getattr(self.user, "jurisdiction", "PA")
        with self.client.post(
            f"{self.hub_url}/platform/usergateway/registeruser",
            json={
                "username": f"perf-test-{uuid.uuid4()}",
                "email": f"perf-{uuid.uuid4()}@test.com",
                "password": os.getenv("LOADTEST_PASSWORD","loadtest-user"),
                "regulation_id": f"US_{jurisdiction}",
                "currency_code": "USD",
                "jurisdiction": jurisdiction,
            },
            name=f"{jurisdiction} Register",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200 or "message" in resp.json():
                resp.failure("Register error")
                self.interrupt()
            body = resp.json()
            self.user.user_id = body["userid"]
            self.user.session_id = body["sessionid"]

    @task
    def geo_verify_lease(self) -> None:
        with self.client.post(
            f"{self.hub_url}/platform/usergateway/geoverify-lease",
            json={
                "userId": self.user.user_id,
                "expiresOn": (
                    datetime.now(timezone.utc) + timedelta(minutes=11)
                ).isoformat(),
                "type": "geoverify-lease",
            },
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure("GeoVerifyLease error")
                self.interrupt()

    @task
    def check_balance_and_limits(self) -> None:
        """Check all responsible-gaming limits in one batch."""
        self.client.get("/platform/balance")
        self.client.get("/platform/limits/daily-wager")
        self.client.get("/platform/limits/weekly-deposit")
        self.client.get("/platform/limits/monthly-wager")
