# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import sys
sys.path.append('../lib/')
from db import *  # ty:ignore[unresolved-import]
from datetime import datetime, timedelta, date
import pandas as pd
import numpy as np

log = []

# Connect to PostgreSQL
envs = os.environ  # ty:ignore[unresolved-reference]
con = init_connection(source='replica', search_path='analytics')  # ty:ignore[unresolved-reference]

# Read SQL
print("SQL Read")
with open('../../postgres/scheduled_reports/daily/daily_commercial_report.sql', 'r') as f:
    query = f.read()

# Execute query
df = pd.read_sql(query, con)

# Save to daily output
df.to_csv('/data/reports/commercial/daily_commercial_report.csv', index=False)

# Weekly archive on Mondays
today = date.today()
if today.strftime("%A") == 'Monday':
    df.to_csv('/data/reports/commercial/weekly/weekly_daily_commercial_report.csv', index=False)

# Monthly archive on 1st of month
if int(today.strftime("%d")) == 1:
    df.to_csv('/data/reports/commercial/monthly/monthly_daily_commercial_report.csv', index=False)
