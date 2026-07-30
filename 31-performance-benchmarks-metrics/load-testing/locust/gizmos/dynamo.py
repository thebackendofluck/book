# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import boto3
import os
import json
from decimal import Decimal

os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"


class DynamoClient:
    def __init__(self):
        self.table = boto3.resource("dynamodb").Table(
            os.getenv("RUN_DB_NAME", "locust-shared-run-db")
        )

    def put_runs(self, corr_id, steps, results):

        completed_actions_ix = len(results)
        completed_actions = steps[:completed_actions_ix]

        item = {"corr_id": corr_id, "steps": completed_actions, "results": results}

        item = json.loads(json.dumps(item), parse_float=Decimal)

        self.table.put_item(Item=item)
