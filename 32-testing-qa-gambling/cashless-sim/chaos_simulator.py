#!/usr/bin/env python
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
Cashless Gaming Chaos Simulator - Load Testing Tool
Multi-threaded load/chaos testing for cashless gaming operations.
Spawns concurrent players performing registrations, deposits,
debits, and credits to stress-test the platform.

Used for performance and chaos testing of the cashless gaming flow.
"""
import urllib3
import json
from tkinter import *
import time
import random
import threading

http = urllib3.PoolManager()
players = []

######################
# CONFIG
######################

hub_url = 'https://platform.perftest.stage.acmetocasino.com/platform/usergateway'
spoke_mi_url = 'https://spoke-platform-mi-cashless.k8s-ext.new.acmetocasino.com/platform/usergateway'
spoke_pa_url = 'https://pa-platform.perftest.stage.acmetocasino.com/platform/usergateway'

ACME_LOGIN = {'url': hub_url,
              'payload': {'type': 'userlogin', 'brand': 'acmebrand', 'username': '', 'password': ''}
              }

ACME_REGISTER = {'url': hub_url,
                 'payload': {
                     "type": "registeruser",
                     "brand": "acmebrand",
                     "jurisdiction": "US-PA",
                     "username": "testplayer2",
                     "password": "testplayer2",
                     "ip": "200.200.200.200",
                     "currency": "USD",
                     "gender": "M",
                     "firstname": "testplayer2",
                     "lastname": "AcmeTest",
                     "salutation": "Mr",
                     "dob": "15-12-2000",
                     "email": "testplayer2@test.com",
                     "phone": "0123567896",
                     "address1": "12352 Acacia Avenue",
                     "address2": "Suburbia2",
                     "town": "",
                     "state": "MI",
                     "ssn": "5555",
                     "ssnMatch": "5555",
                     "postalcode": "12345",
                     "country": "US",
                     "language": "en",
                     "geolocation": "US",
                     "referral": "Google",
                     "enabled": True
                 }
                 }

ACME_VERIFY_USER = {
    'url': hub_url,
    'payload': {
        "type": "verifyuser",
        "brand": "acmebrand",
        "username": "testplayer"
    }
}

ACME_GEO_VERIFY_USER = {
    'url': hub_url,
    'payload': {
        "type": "geoverify-lease",
        "userId": 2000024,
        "expiresOn": "2022-06-10T07:45:20.788",
        "jurisdiction": "US-PA"
    }
}


ACME_GET_BALANCE = {'url': hub_url,
                    'payload': {'type': 'getbalance', 'sessionid': ''}
                    }


ACME_DEPOSIT = {
    'url': hub_url,
    'payload': {
        "type": "deposit",
        "method": "credit",
        "userId": 2000024,
        "amount": 100,
        "provider": "internal_test",
        "comments": "Internal test deposit",
        "ref": "AAABeCYaln4AAAAAAB6Eltli5VtJ1yAEXvyp9zdB1fg"
    }
}

ACME_WITHDRAW = {
    'url': hub_url,
    'payload': {
        "type": "withdraw",
        "sessionid": "bc075400c2d6ec48b988931a6a5dc112",
        "amount": 100.00,
        "password": "mypassword",
        "details": "12341212"
    }
}

ACME_DEBIT = {
    'url': spoke_pa_url,
    'payload': {
        "type": "debit",
        "userId": 2000022,
        "amount": 5.00,
        "txnid": "cs-12347",
        "roundid": "cs-94534",
        "systemid": "2",
        "gameid": 1
    }
}

ACME_CREDIT = {
    'url': spoke_pa_url,
    'payload': {
        "type": "credit",
        "userId": 2000022,
        "amount": 5000.00,
        "txnid": "cs-12347",
        "roundid": "cs-94534",
        "systemid": "2",
        "gameid": 1
    }
}

ACME_MANUAL_ACCOUNT = {
    'url': hub_url,
    'payload': {
        "type": "manualaccountchange",
        "method": "credit",
        "userId": 430124,
        "amount": 100,
        "accountTypeId": 1,
        "adjustrollover": False,
        "operator": "Test",
        "reason": "Cashless Test",
    }
}

######################
# DEPOSIT LIMIT
######################

ACME_SET_TRANSFER_LIMIT = {
    'url': hub_url,
    'payload': {
        'type': 'deposit-limit-update',
        'sessionid': '',
        'depositlimit': {
            'period': 'daily',
            'value': 0
        },
        'password': 'testplayer'
    }
}

######################
# FORM FIELDS
######################
fields = {'Jurisdiction': 'US-PA', 'State': 'PA', 'Username': '', 'Password': '', "User ID": '',
          'Session ID': '', 'Balance': 0, 'Amount': 100, 'TX ID': "cs-12345",
          "Limit (cents)": "100", "Limit Period": "daily", "Response": "",
          "# threads": "20", "#Registrations per Thread": "100"}


#############################
# Load Testing Calls
#############################

def launch_loadtest(entries):
    threads = {}
    t = time.time()
    num_threads = int(entries["# threads"].get())
    for i in range(num_threads):
        threads[i] = threading.Thread(target=reg_login_deposit_lt,
                                      args=([int(entries["#Registrations per Thread"].get())]))
    for i in range(num_threads):
        threads[i].start()
    for i in range(num_threads):
        threads[i].join()

    for i in range(num_threads):
        if i == 2:
            threads[i] = threading.Thread(target=reg_login_deposit_lt,
                                          args=([int(entries["#Registrations per Thread"].get())]))
        else:
            threads[i] = threading.Thread(target=debits_credits_lt,
                                          args=([int(entries["#Registrations per Thread"].get())]))
    for i in range(num_threads):
        threads[i].start()
    for i in range(num_threads):
        threads[i].join()

    print(time.time() - t)


def reg_login_deposit_lt(num_registrations):
    for i in range(num_registrations):
        try:
            user_name = 'chaos_' + str(int(round(time.time() * 1000))) + str(
                random.randint(10000000000, 9999999999999))
            register_lt(user_name)
            verify_user_lt(user_name)
            user_id, session_id = login_lt(user_name)
            geo_verify_user_lt(user_id)
            deposit_lt(user_id, session_id)
            players.append({"user_id": user_id, "user_name": user_name, "session_id": session_id})
        except Exception as e:
            print(str(e) + str(user_name))


def debits_credits_lt(num_registrations):
    for player in players:
        try:
            tx = str(int(round(time.time() * 1000))) + str(random.randint(10000000000, 9999999999999))
            debit_lt(player, tx)
            getbalance_lt(player)
            credit_lt(player, tx)
            getbalance_lt(player)
        except Exception as e:
            print(str(e) + str(player))


def register_lt(user_name):
    ACME_REGISTER['payload']['jurisdiction'] = "US-PA"  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['state'] = "PA"  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['username'] = user_name  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['password'] = user_name  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['firstname'] = user_name  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['address1'] = user_name  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['dob'] = str(random.randint(1901, 2000)) + "-" + str(  # ty:ignore[invalid-assignment]
        random.randint(1, 12)) + "-" + str(random.randint(1, 28))
    ACME_REGISTER['payload']['postalcode'] = str(random.randint(10000, 99999))  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['email'] = user_name + "@test.com"  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_REGISTER["url"], body=json.dumps(ACME_REGISTER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


def login_lt(user_name):
    ACME_LOGIN['payload']['username'] = user_name  # ty:ignore[invalid-assignment]
    ACME_LOGIN['payload']['password'] = user_name  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_LOGIN["url"], body=json.dumps(ACME_LOGIN["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    data = json.loads(r.data)
    return data['userid'], data["sessionid"]


def verify_user_lt(user_name):
    ACME_VERIFY_USER['payload']['username'] = user_name  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_VERIFY_USER["url"], body=json.dumps(ACME_VERIFY_USER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


def geo_verify_user_lt(user_id):
    ACME_GEO_VERIFY_USER['payload']['userId'] = user_id  # ty:ignore[invalid-assignment]
    ACME_GEO_VERIFY_USER['payload']['jurisdiction'] = "US-PA"  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_GEO_VERIFY_USER["url"], body=json.dumps(ACME_GEO_VERIFY_USER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


def deposit_lt(user_id, session_id):
    ACME_DEPOSIT['payload']['userId'] = user_id  # ty:ignore[invalid-assignment]
    ACME_DEPOSIT['payload']['ref'] = session_id  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_DEPOSIT["url"], body=json.dumps(ACME_DEPOSIT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


def debit_lt(player, tx):
    ACME_DEBIT['payload']['userId'] = player["user_id"]  # ty:ignore[invalid-assignment]
    ACME_DEBIT['payload']['amount'] = 10  # ty:ignore[invalid-assignment]
    ACME_DEBIT['payload']['txnid'] = tx  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_DEBIT["url"], body=json.dumps(ACME_DEBIT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


def credit_lt(player, tx):
    ACME_CREDIT['payload']['userId'] = player["user_id"]  # ty:ignore[invalid-assignment]
    ACME_CREDIT['payload']['amount'] = 10  # ty:ignore[invalid-assignment]
    ACME_CREDIT['payload']['txnid'] = tx  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_CREDIT["url"], body=json.dumps(ACME_CREDIT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


def getbalance_lt(player):
    ACME_GET_BALANCE['payload']['sessionid'] = player['session_id']  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_GET_BALANCE["url"], body=json.dumps(ACME_GET_BALANCE["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})


#############################
# Synchronous Calls
#############################

def register(entries):
    user_id = 'chaos_' + str(int(round(time.time() * 1000)))
    ACME_REGISTER['payload']['jurisdiction'] = entries['Jurisdiction'].get()  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['state'] = entries['State'].get()  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['username'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['password'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['firstname'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['address1'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['dob'] = str(random.randint(1901, 2000)) + "-" + str(  # ty:ignore[invalid-assignment]
        random.randint(1, 12)) + "-" + str(random.randint(1, 28))
    ACME_REGISTER['payload']['postalcode'] = str(random.randint(10000, 99999))  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['email'] = user_id + "@test.com"  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_REGISTER["url"], body=json.dumps(ACME_REGISTER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    data = json.loads(r.data)
    update_entry(entries, 'Username', user_id)
    update_entry(entries, 'Password', user_id)
    update_entry(entries, 'Response', str(data))
    verify_user(entries)
    login(entries)
    geo_verify_user(entries)


def verify_user(entries, print_response=False):
    ACME_VERIFY_USER['payload']['username'] = entries['Username'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_VERIFY_USER["url"], body=json.dumps(ACME_VERIFY_USER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    if print_response:
        update_entry(entries, 'Response', str(json.loads(r.data)))


def geo_verify_user(entries, print_response=False):
    ACME_GEO_VERIFY_USER['payload']['userId'] = int(entries['User ID'].get())  # ty:ignore[invalid-assignment]
    ACME_GEO_VERIFY_USER['payload']['jurisdiction'] = entries['Jurisdiction'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_GEO_VERIFY_USER["url"], body=json.dumps(ACME_GEO_VERIFY_USER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    if print_response:
        update_entry(entries, 'Response', str(json.loads(r.data)))


def login(entries, print_response=False):
    ACME_LOGIN['payload']['username'] = entries['Username'].get()  # ty:ignore[invalid-assignment]
    ACME_LOGIN['payload']['password'] = entries['Password'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_LOGIN["url"], body=json.dumps(ACME_LOGIN["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    data = json.loads(r.data)
    if print_response:
        update_entry(entries, 'Response', str(json.loads(r.data)))
    session, balance, user_id = data["sessionid"], data["balances"]["cash"], data['userid']
    update_entry(entries, 'Balance', balance)
    update_entry(entries, 'Session ID', session)
    update_entry(entries, 'User ID', user_id)


def getbalance(entries, print_response=False):
    ACME_GET_BALANCE['payload']['sessionid'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_GET_BALANCE["url"], body=json.dumps(ACME_GET_BALANCE["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    balance = json.loads(r.data)['balances']['cash']
    update_entry(entries, 'Balance', balance)
    if print_response:
        update_entry(entries, 'Response', str(json.loads(r.data)))


def set_player_limit(entries):
    ACME_SET_TRANSFER_LIMIT['payload']['sessionid'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    ACME_SET_TRANSFER_LIMIT['payload']['depositlimit']['value'] = int(entries['Limit (cents)'].get())  # ty:ignore[invalid-argument-type, invalid-assignment]
    ACME_SET_TRANSFER_LIMIT['payload']['depositlimit']['period'] = entries['Limit Period'].get()  # ty:ignore[invalid-argument-type, invalid-assignment]
    ACME_SET_TRANSFER_LIMIT['payload']['password'] = entries['Password'].get()  # ty:ignore[invalid-assignment]

    r = http.request('POST', ACME_SET_TRANSFER_LIMIT["url"],  # ty:ignore[invalid-argument-type]
                     body=json.dumps(ACME_SET_TRANSFER_LIMIT["payload"]),
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    getbalance(entries)


def manual_account(entries):
    ACME_MANUAL_ACCOUNT['payload']['userId'] = int(entries['User ID'].get())  # ty:ignore[invalid-assignment]
    ACME_MANUAL_ACCOUNT['payload']['ref'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_MANUAL_ACCOUNT["url"], body=json.dumps(ACME_MANUAL_ACCOUNT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    time.sleep(0.5)
    getbalance(entries)


def deposit(entries):
    ACME_DEPOSIT['payload']['userId'] = int(entries['User ID'].get())  # ty:ignore[invalid-assignment]
    ACME_DEPOSIT['payload']['ref'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_DEPOSIT["url"], body=json.dumps(ACME_DEPOSIT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    time.sleep(0.5)
    getbalance(entries)


def withdraw(entries):
    ACME_WITHDRAW['payload']['sessionid'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    ACME_WITHDRAW['payload']['password'] = entries['Password'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_WITHDRAW["url"], body=json.dumps(ACME_WITHDRAW["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    time.sleep(0.5)
    getbalance(entries)


def debit(entries):
    ACME_DEBIT['payload']['userId'] = int(entries['User ID'].get())  # ty:ignore[invalid-assignment]
    ACME_DEBIT['payload']['amount'] = int(entries['Amount'].get())  # ty:ignore[invalid-assignment]
    ACME_DEBIT['payload']['txnid'] = entries['TX ID'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_DEBIT["url"], body=json.dumps(ACME_DEBIT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    time.sleep(0.5)
    getbalance(entries)


def credit(entries):
    ACME_CREDIT['payload']['userId'] = int(entries['User ID'].get())  # ty:ignore[invalid-assignment]
    ACME_CREDIT['payload']['amount'] = int(entries['Amount'].get())  # ty:ignore[invalid-assignment]
    ACME_CREDIT['payload']['txnid'] = entries['TX ID'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_CREDIT["url"], body=json.dumps(ACME_CREDIT["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    time.sleep(0.5)
    getbalance(entries)


def make_form(form_root, form_fields):
    entries = {}
    for field in form_fields:
        row = Frame(form_root)
        lab = Label(row, width=25, text=field + ": ", anchor='w')
        ent = Entry(row, width=175)
        ent.insert(0, form_fields[field])
        row.pack(side=TOP, fill=X, padx=5, pady=5)
        lab.pack(side=LEFT)
        ent.pack(side=RIGHT, expand=YES, fill=X)
        entries[field] = ent
    return entries


def update_entry(entries, field, value):
    entries[field].delete(0, END)
    entries[field].insert(0, value)


######################
# EXECUTE
######################

if __name__ == '__main__':
    root = Tk()
    root.title("AcmetoCasino - Cashless Chaos Simulator")
    ents = make_form(root, fields)
    root.bind('<Return>', (lambda event, e=ents: fetch(e)))  # ty:ignore[unresolved-reference]
    bnew_account = Button(root, text='New Account',
                          command=(lambda e=ents: register(e)))
    bnew_account.pack(side=LEFT, padx=5, pady=5)
    blogin = Button(root, text='Login',
                    command=(lambda e=ents: login(e, print_response=True)))
    blogin.pack(side=LEFT, padx=5, pady=5)
    bdeposit = Button(root, text='Deposit',
                      command=(lambda e=ents: deposit(e)))
    bdeposit.pack(side=LEFT, padx=5, pady=5)
    bmanualaccount = Button(root, text='Man. Acc. Change',
                            command=(lambda e=ents: manual_account(e)))
    bmanualaccount.pack(side=LEFT, padx=5, pady=5)
    bwithdraw = Button(root, text='Withdraw',
                       command=(lambda e=ents: withdraw(e)))
    bwithdraw.pack(side=LEFT, padx=5, pady=5)
    bdebit = Button(root, text='Debit',
                    command=(lambda e=ents: debit(e)))
    bdebit.pack(side=LEFT, padx=5, pady=5)
    bcredit = Button(root, text='Credit',
                     command=(lambda e=ents: credit(e)))
    bcredit.pack(side=LEFT, padx=5, pady=5)
    bbalance = Button(root, text='Get Balance',
                      command=(lambda e=ents: getbalance(e, print_response=True)))
    bbalance.pack(side=LEFT, padx=5, pady=5)
    blimit = Button(root, text='Set Player Limit',
                    command=(lambda e=ents: set_player_limit(e)))
    blimit.pack(side=LEFT, padx=5, pady=5)
    blimit = Button(root, text='Launch Load Test',
                    command=(lambda e=ents: launch_loadtest(e)))
    blimit.pack(side=LEFT, padx=5, pady=5)
    bquit = Button(root, text='Quit', command=root.quit)
    bquit.pack(side=LEFT, padx=5, pady=5)
    root.mainloop()
