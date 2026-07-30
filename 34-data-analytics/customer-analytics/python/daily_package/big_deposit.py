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
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import re
from pathlib import Path

log = []
save_dir = Path('/data/reports/deposits')
logs_dir = Path('/data/logs')

# Connect to PostgreSQL
envs = os.environ  # ty:ignore[unresolved-reference]
con = init_connection(source='replica', search_path='analytics')  # ty:ignore[unresolved-reference]

# Read SQL and split weekday/weekend queries
print("SQL Read")
with open('../../postgres/scheduled_reports/daily/big_deposit/Big_Dep.sql', 'r') as f:
    query = f.read()

weekday, weekend = query.split(';')[:2]
date = datetime.today()
date = date.replace(hour=0, minute=0, second=0)

query = ""

monday = date.weekday() == 0
friday = date.weekday() == 4

if monday:
    weekend_flag = True
    date -= timedelta(days=3)
    date_str = date.strftime("%d-%m-%Y")
    query = re.sub(r"\d{2}-\d{2}-\d{4}", date_str, weekend)
else:
    weekend_flag = False
    date -= timedelta(days=1)
    date_str = date.strftime("%d-%m-%Y")
    query = re.sub(r"\d{2}-\d{2}-\d{4}", date_str, weekday)

df = pd.read_sql(query, con)


def get_deposit_amount(user_id, date):
    """Cross-check deposit totals against daily player stats."""
    date_start = date.strftime("%Y-%m-%d")

    if weekend_flag:
        date_end = date + timedelta(days=2, hours=23, minutes=59, seconds=59)
        date_end = date_end.strftime('%Y-%m-%d %H:%M:%S')
    else:
        date_end = date + timedelta(days=0, hours=23, minutes=59, seconds=59)
        date_end = date_end.strftime('%Y-%m-%d %H:%M:%S')

    user_day_query = f"""
        SELECT sum(deposit) AS deposit FROM analytics_dw.daily_player_stats dps
        WHERE dps.user_id = {user_id}
        AND dps.on_date BETWEEN timestamp '{date_start}' AND timestamp '{date_end}'
    """
    user_dps = pd.read_sql(user_day_query, con)
    return user_dps['deposit'].item()


if weekend_flag:
    date += timedelta(days=2)
date_str = date.strftime('%Y%m%d')

save_path = save_dir / f'{date_str}_Big_Deposit.csv'
df.to_csv(save_path, index=False)

# Send email notification
to = ["analytics-team@acmetocasino.com"]
cc = []

subject = "Big Deposit Report"
email_var_date = "last weekend" if monday else "yesterday"
body = f"""
Hi,

Good morning.

Attached please find the CSV detailing {email_var_date}'s Big Depositors.

Regards,
Analytics Department
"""

print(f"Report saved to: {save_path}")
