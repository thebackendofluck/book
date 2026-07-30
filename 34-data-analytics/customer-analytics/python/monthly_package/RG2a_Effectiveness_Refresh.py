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
import numpy as np
from datetime import datetime, timedelta
import pandas as pd

log = []
df = pd.DataFrame()
logs_dir = '/data/logs'

# PostgreSQL Connection
try:
    envs = os.environ  # ty:ignore[unresolved-reference]
    con = init_connection(source='replica', search_path='analytics')  # ty:ignore[unresolved-reference]
except Exception:
    log.append('Connection to Database engine Failed')
    print("no connection established")

# SQL Query -- RG2a Effectiveness analysis for responsible gaming monitoring
try:
    print("SQL Read")
    with open('../../postgres/adhoc_analysis/RG/RG2a_Effectiveness.sql', 'r') as f:
        query = f.read()
    df = pd.read_sql(query, con)
    print(df)

    date_str = datetime.now().strftime("%Y%m%d")

    # Append to cumulative RG effectiveness data
    save_path = '/data/reports/rg/RG2a_Effectiveness_Raw_Data.csv'
    df.to_csv(save_path, mode='a', index=False, header=False)

except Exception as e:
    print(e)
    log.append('SQL Query and save csv failed')

# Email notification
try:
    date_str = datetime.now().strftime("%Y%m%d")
    to = ['rg-team@acmetocasino.com']
    cc = []

    subject = f"{date_str} - RG2a_Effectiveness"

    if df.empty:
        body = """
        Hi Team,

        Good morning.

        There are no RG2a Interactions.

        Regards,
        Analytics Department
        """
    else:
        body = """
        Hi Team,

        Good morning.

        Please note that the RG2a Effectiveness Report has been updated.

        Regards,
        Analytics Department
        """

    print(f"Report generated: {subject}")

except Exception as e:
    print(e)
    log.append('Report generation failed')
