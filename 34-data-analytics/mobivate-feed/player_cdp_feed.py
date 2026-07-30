#!/bin/python
# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Player behavioral data extraction and SFTP export to CDP (Optimove/Mobivate).
Extracts active, churned, and VIP player segments for SMS marketing campaigns
with responsible gaming exclusions and marketing preference compliance.

Adapted from a production iGaming platform (AcmetoCasino).
"""
import os
import psycopg2
import csv
import datetime
import sys
import pysftp  # ty:ignore[unresolved-import]
import time
from optparse import OptionParser
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

cnopts = pysftp.CnOpts()
cnopts.hostkeys = None

parser = OptionParser()
parser.add_option("-b", type="string", dest="brandID", help="Brand ID", metavar="ID")
parser.add_option("-s", type="string", dest="sftpHost", help="CDP SFTP Hostname",
                  default="doNotTransfer")
parser.add_option("-u", type="string", dest="sftpUsername", help="CDP SFTP Username", default="Empty")
parser.add_option("-p", type="string", dest="sftpPassword", help="CDP SFTP Password", default="Empty")
parser.add_option("-x", type="string", dest="filePrefix", help="Prefix for output filenames", default="")
parser.add_option("-l", type="string", dest="fileLocation", help="Output location",
                  default="./")
parser.add_option("-d", type="int", dest="to_days", help="Days to go back, default 1", default=1)
parser.add_option("-q", type="string", dest="queryList", help="Comma-separated list of queries to run",
                  metavar="LIST", default="ALL")
parser.add_option("-H", type="string", dest="csvHeaders", help="Y to include headers", default="Y")

(options, args) = parser.parse_args()

# CDP SFTP configuration (credentials from environment)
sftpUsername = os.getenv('CDP_SFTP_USER', 'cdp_user')
sftpHost = os.getenv('CDP_SFTP_HOST', 'sftp.cdp-provider.example.com')
sftpPort = int(os.getenv('CDP_SFTP_PORT', '33122'))
sftpKeyFile = os.getenv('CDP_SFTP_KEY', '~/.ssh/id_rsa')

# Date formatting
action_date = datetime.datetime.now()
date_string = action_date.strftime("%Y_%m_%d")
sql_date_string = action_date.strftime("%Y-%m-%d")

qry = {}

# Active players: logged in within 30 days, funded, SMS opted-in, RG-safe
qry["01_Active_Players"] = """
select coalesce(u.deprecated_user_id, u.id)  as user_id,
       i.phone                                as phone,
       i.firstname                            as firstname,
       u.name                                 as username,
       trim(replace(replace(concat_ws('', coalesce(bs.setting_value, dbs.setting_value), ' ')::text,
                            '{baseurl}', coalesce(coalesce(b.web, '?'), '')),
                    '{marketing_token}',
                    coalesce(coalesce((select min(t.token_value)
                                       from casino_core.user_tokens as t
                                       where t.user_id = u.id
                                         and t.token_type = 'MARKETING_PREFERENCES'
                                         and t.status in ('PENDING', 'ACTIVE')), '?'), ''))) as unique_sms_optout_link,
       ac.currency
from casino_core.users as u
         inner join casino_core.user_info as i on u.id = i.userid and i.testaccount = false
         inner join casino_core.brands as b on u.affiliateid = b.id
         inner join casino_core.user_accounts as ac on ac.userid = u.id and ac.typeid = 1
         inner join casino_core.user_marketing_preferences as ump on u.id = ump.user_id
         inner join casino_core.marketing_preferences as mp on ump.brand_marketing_prefs = mp.id
                    and ump.user_id = mp.user_id
         left outer join casino_core.brand_settings as bs on u.affiliateid = bs.brand_id
                    and bs.setting_name = 'sms-prefs-url'
         left outer join casino_core.brand_settings as dbs on dbs.brand_id is null
                    and dbs.setting_name = 'sms-prefs-url'
         left outer join casino_core.excluded_dialling_codes edc on
                    substring(i.phone, 1, 3) = edc.phone_prefix
where i.phone_type in ('MOBILE', 'XXXFIXED_LINE_OR_MOBILE')
  and mp.sms = true
  and u.activated = true
  and u.exclude_from_marketing = false
  and u.funded = true
  and u.enabled = true
  and u.locked = false
  and coalesce(lower(casino_core.extractuserparam(i.params, 'BONUS_AVAILABLE')), 'on') != 'off'
  and lastlogin >= now() - (30::numeric || ' days')::interval
  -- Exclude RG3 locked players
  and not exists(
        select 1 from casino_core.user_lock ul
        where ul.lock_type_id in ('RG3', 'MATCHED_RG3')
          and UPPER(ul.status) not in ('COMPLETED', 'CANCELLED')
          and ul.user_id = u.id)
  -- Exclude players with bonus restrictions
  and not exists(
        select 1 from casino_core.user_flags uf
             join casino_core.flags fl on uf.flag_id = fl.id
        where uf.value is true
          and fl.name in ('loyalty_team', 'excluded_from_bonus')
          and uf.user_id = u.id)
group by (edc.phone_prefix, 1, i.phone, i.firstname, unique_sms_optout_link, u.id, i.country, u.name, currency)
having count(edc.id) = 0
order by u.id
"""

# Churned players: 30-90 days since last login
qry["02_Churn_30_90_Days"] = """
select coalesce(u.deprecated_user_id, u.id) as user_id,
       i.phone, i.firstname, u.name as username, ac.currency
from casino_core.users as u
         inner join casino_core.user_info as i on u.id = i.userid and i.testaccount = false
         inner join casino_core.brands as b on u.affiliateid = b.id
         inner join casino_core.user_accounts as ac on ac.userid = u.id and ac.typeid = 1
         inner join casino_core.user_marketing_preferences as ump on u.id = ump.user_id
         inner join casino_core.marketing_preferences as mp on ump.brand_marketing_prefs = mp.id
                    and ump.user_id = mp.user_id
         left outer join casino_core.excluded_dialling_codes edc on
                    substring(i.phone, 1, 3) = edc.phone_prefix
where i.phone_type in ('MOBILE', 'XXXFIXED_LINE_OR_MOBILE')
  and mp.sms = true
  and u.activated = true
  and u.exclude_from_marketing = false
  and u.funded = true
  and u.enabled = true
  and u.locked = false
  and i.lastlogin between (now() - (90::numeric || ' days')::interval)
                      and (now() - (30::numeric || ' days')::interval)
  and not exists(
        select 1 from casino_core.user_lock ul
        where ul.lock_type_id in ('RG3', 'MATCHED_RG3')
          and UPPER(ul.status) not in ('COMPLETED', 'CANCELLED')
          and ul.user_id = u.id)
group by (edc.phone_prefix, 1, i.phone, i.firstname, u.id, u.name, currency)
having count(edc.id) = 0
order by u.id
"""

# Connect to database (read-only replica)
connection = psycopg2.connect(user=DB_USER, password=DB_PASSWORD,
                              host=DB_HOST, port=DB_PORT, database=DB_NAME)

genlist = ''

for q in sorted(qry.keys()):
    i = q[2:]

    if (i in options.queryList) or (options.queryList == "ALL"):
        outputfile = options.fileLocation + ('{1}' + i + '_{0}.csv').format(date_string, options.filePrefix)
        print(f"Generating: {outputfile}")
        genlist += i + ' '

        cursor = connection.cursor()
        cursor.execute(qry[q])
        FILE = open(outputfile, "w")
        output = csv.writer(FILE, dialect='excel')

        if options.csvHeaders == "Y":
            header = [h[0] for h in cursor.description]
            output.writerow(header)

        for row in cursor:
            output.writerow(row)
        cursor.close()
        FILE.close()

        # Upload to CDP SFTP
        print(outputfile)
        with pysftp.Connection(sftpHost, username=sftpUsername, port=sftpPort,
                               private_key=sftpKeyFile, cnopts=cnopts) as sftp:
            sftp.put(outputfile, confirm=False)

connection.close()
print(f"Completed: {genlist}")
