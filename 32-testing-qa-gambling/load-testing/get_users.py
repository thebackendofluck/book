#!/usr/local/bin/python3
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import boto3  # ty:ignore[unresolved-import]
import sys
import os
import psycopg2
import json
import csv
import botocore  # ty:ignore[unresolved-import]
import datetime

from dateutil.tz import tzlocal
from botocore.exceptions import ClientError  # ty:ignore[unresolved-import]
from psycopg2.extras import RealDictCursor

users_list = []
ACCESS_KEY = ""
SECRET_KEY = ""
SESSION_TOKEN = ""
REGION = "us-east-1"
SECRET_NAME = "sb/drill/backend/db"


def get_secret():

    SECRET_NAME = "sb/drill/backend/db"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=REGION)
    # In this sample we only handle the specific exceptions for the 'GetSecretValue' API.
    # See https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
    # We rethrow the exception by default.

    try:
        print("Attempting to get secrets")
        get_secret_value_response = client.get_secret_value(SecretId=SECRET_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] == "DecryptionFailureException":
            # Secrets Manager can't decrypt the protected secret text using the provided KMS key.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "InternalServiceErrorException":
            # An error occurred on the server side.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "InvalidParameterException":
            # You provided an invalid value for a parameter.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "InvalidRequestException":
            # You provided a parameter value that is not valid for the current state of the resource.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
        elif e.response["Error"]["Code"] == "ResourceNotFoundException":
            # We can't find the resource that you asked for.
            # Deal with the exception here, and/or rethrow at your discretion.
            raise e
    else:
        if "SecretString" in get_secret_value_response:
            secret = get_secret_value_response["SecretString"]

        else:
            secret = base64.b64decode(get_secret_value_response["SecretBinary"])  # ty:ignore[unresolved-reference]

    return json.loads(secret)


def get_users_list(secret):

    endpoint = secret["host"]
    port = secret["port"]
    username = secret["username"]
    password = secret["password"]
    dbname = secret["dbname"]

    session = boto3.session.Session()
    client = boto3.client(service_name="rds", region_name=REGION)

    print("connecting to db...")
    try:
        conn = psycopg2.connect(
            host=endpoint,
            port=port,
            database=dbname,
            user=username,
            password=password,
            sslmode="prefer",
            sslrootcert="[full path]rds-combined-ca-bundle.pem",
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)  # ty:ignore[possibly-missing-attribute]
        cur.execute(
            """SELECT username FROM users_player WHERE username NOT LIKE 'test%' AND created > '2025-09-06 00:00:00+00';"""
        )
        query_results = [r[0] for r in cur.fetchall()]
    except Exception as e:
        print("Database connection failed due to {}".format(e))

    return query_results


if __name__ == "__main__":

    db_secrets = get_secret()
    users_list = get_users_list(db_secrets)

    with open("users.csv", "w+") as user_file:
        write = csv.writer(user_file)
        for user in users_list:
            write.writerow([user])
        print("users list generated!!!")
