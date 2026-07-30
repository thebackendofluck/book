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
Cashless Gaming Simulator - Interactive GUI
Simulates cashless gaming operations (to-machine, from-machine transfers)
against the platform's User Gateway API.

Used for manual QA testing of the cashless gaming flow in regulated US markets.
"""
import urllib3
import json
from tkinter import *
import time

http = urllib3.PoolManager()

######################
# CONFIG
######################

hub_url = 'https://platform-cashless.k8s-ext.new.acmetocasino.com/platform/usergateway'
spoke_mi_url = 'https://spoke-platform-mi-cashless.k8s-ext.new.acmetocasino.com/platform/usergateway'
spoke_pa_url = 'https://spoke-platform-pa-cashless.k8s-ext.new.acmetocasino.com/platform/usergateway'
sasSerial = '904666'  # SAS serial number for slot machine identification

ACME_LOGIN = {'url': hub_url,
              'payload': {'type': 'userlogin', 'brand': 'acmebrand', 'username': '', 'password': ''}
              }

ACME_REGISTER = {'url': hub_url,
                 'payload': {
                     "type": "registeruser",
                     "brand": "acmebrand",
                     "jurisdiction": "US-MI",
                     "username": "testplayer2",
                     "password": "testplayer2",
                     "ip": "200.200.200.200",
                     "currency": "USD",
                     "gender": "M",
                     "firstName": "testplayer2",
                     "lastName": "AcmeTest",
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
                     "postcode": "12345",
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

ACME_GET_BALANCE = {'url': hub_url,
                    'payload': {'type': 'getbalance', 'sessionid': ''}
                    }

ACME_TO_MACHINE = {'url': hub_url,
                   'payload': {
                       'type': 'to-machine',
                       'sessionid': '',
                       'amount': 0,
                       'transactionUID': 0,
                       'sasSerial': sasSerial,
                       'targetUrl': ""
                   }
                   }

ACME_FROM_MACHINE = {'url': hub_url,
                     'payload': {
                         'type': 'from-machine',
                         'amount': 0,
                         'transactionUID': 0,
                         'sasSerial': sasSerial,
                         'targetUrl': ""
                     }
                     }

ACME_DEPOSIT = {
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
# TRANSFER TO MACHINE LIMIT
######################

ACME_SET_TRANSFER_LIMIT = {
    'url': hub_url,
    'payload': {
        'type': 'transfertomachine-limit-update',
        'sessionid': '',
        'transfertomachinelimit': {
            'period': 'daily',
            'value': 0
        },
        'password': 'testplayer'
    }
}

######################
# FORM FIELDS
######################
fields = {'Jurisdiction': 'US-MI', 'State': 'MI', 'Username': '', 'Password': '', "User ID": '',
          'Session ID': '', 'Balance': 0, 'To-machine amount(cents)': '100', 'From-machine amount(cents)': '150',
          'TransactionUID': str(int(round(time.time() * 1000))), 'sasSerial': sasSerial,
          "targetUrl": "https://cashless-endpoint.example.com", "Limit (cents)": "100",
          "Limit Period": "daily", "Response": ""}


######################
# FORM FUNCTIONS
######################
def register(entries):
    user_id = 'testplayer_' + str(int(round(time.time() * 1000)))
    ACME_REGISTER['payload']['jurisdiction'] = entries['Jurisdiction'].get()  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['state'] = entries['State'].get()  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['username'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['password'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['firstName'] = user_id  # ty:ignore[invalid-assignment]
    ACME_REGISTER['payload']['email'] = user_id + "@test.com"  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_REGISTER["url"], body=json.dumps(ACME_REGISTER["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    data = json.loads(r.data)
    update_entry(entries, 'Username', user_id)
    update_entry(entries, 'Password', user_id)
    update_entry(entries, 'Response', str(data))
    verify_user(entries)
    login(entries)


def verify_user(entries, print_response=False):
    ACME_VERIFY_USER['payload']['username'] = entries['Username'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_VERIFY_USER["url"], body=json.dumps(ACME_VERIFY_USER["payload"]),  # ty:ignore[invalid-argument-type]
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


def to_machine(entries):
    ACME_TO_MACHINE['payload']['sessionid'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    ACME_TO_MACHINE['payload']['amount'] = int(entries['To-machine amount(cents)'].get())  # ty:ignore[invalid-assignment]
    ACME_TO_MACHINE['payload']['sasSerial'] = entries['sasSerial'].get()  # ty:ignore[invalid-assignment]
    ACME_TO_MACHINE['payload']['targetUrl'] = entries['targetUrl'].get()  # ty:ignore[invalid-assignment]
    transaction_uid = int(round(time.time() * 1000))
    ACME_TO_MACHINE['payload']['transactionUID'] = transaction_uid  # ty:ignore[invalid-assignment]
    update_entry(entries, 'TransactionUID', str(transaction_uid))

    r = http.request('POST', ACME_TO_MACHINE["url"], body=json.dumps(ACME_TO_MACHINE["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    print(str(json.loads(r.data)))
    update_entry(entries, 'Response', str(json.loads(r.data)))
    getbalance(entries)


def from_machine(entries):
    ACME_FROM_MACHINE['payload']['amount'] = int(entries['From-machine amount(cents)'].get())  # ty:ignore[invalid-assignment]
    ACME_FROM_MACHINE['payload']['transactionUID'] = int(entries['TransactionUID'].get())  # ty:ignore[invalid-assignment]
    ACME_FROM_MACHINE['payload']['sasSerial'] = entries['sasSerial'].get()  # ty:ignore[invalid-assignment]
    ACME_FROM_MACHINE['payload']['targetUrl'] = entries['targetUrl'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_FROM_MACHINE["url"], body=json.dumps(ACME_FROM_MACHINE["payload"]),  # ty:ignore[invalid-argument-type]
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    getbalance(entries)


def set_player_limit(entries):
    ACME_SET_TRANSFER_LIMIT['payload']['sessionid'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    ACME_SET_TRANSFER_LIMIT['payload']['transfertomachinelimit']['value'] = int(entries['Limit (cents)'].get())  # ty:ignore[invalid-argument-type, invalid-assignment]
    ACME_SET_TRANSFER_LIMIT['payload']['transfertomachinelimit']['period'] = entries['Limit Period'].get()  # ty:ignore[invalid-argument-type, invalid-assignment]

    r = http.request('POST', ACME_SET_TRANSFER_LIMIT["url"],  # ty:ignore[invalid-argument-type]
                     body=json.dumps(ACME_SET_TRANSFER_LIMIT["payload"]),
                     headers={'Content-Type': 'application/json'})
    update_entry(entries, 'Response', str(json.loads(r.data)))
    getbalance(entries)


def add_funds(entries):
    ACME_DEPOSIT['payload']['userId'] = int(entries['User ID'].get())  # ty:ignore[invalid-assignment]
    ACME_DEPOSIT['payload']['ref'] = entries['Session ID'].get()  # ty:ignore[invalid-assignment]
    r = http.request('POST', ACME_DEPOSIT["url"], body=json.dumps(ACME_DEPOSIT["payload"]),  # ty:ignore[invalid-argument-type]
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
    root.title("AcmetoCasino - Cashless Gaming Simulator")
    ents = make_form(root, fields)
    root.bind('<Return>', (lambda event, e=ents: fetch(e)))  # ty:ignore[unresolved-reference]
    bnew_account = Button(root, text='New Account',
                          command=(lambda e=ents: register(e)))
    bnew_account.pack(side=LEFT, padx=5, pady=5)
    blogin = Button(root, text='Login',
                    command=(lambda e=ents: login(e, print_response=True)))
    blogin.pack(side=LEFT, padx=5, pady=5)
    bdeposit = Button(root, text='Deposit $100',
                      command=(lambda e=ents: add_funds(e)))
    bdeposit.pack(side=LEFT, padx=5, pady=5)
    bbalance = Button(root, text='Get Balance',
                      command=(lambda e=ents: getbalance(e, print_response=True)))
    bbalance.pack(side=LEFT, padx=5, pady=5)
    bto_machine = Button(root, text='To Machine',
                         command=(lambda e=ents: to_machine(e)))
    bto_machine.pack(side=LEFT, padx=5, pady=5)
    bfrom_machine = Button(root, text='From Machine',
                           command=(lambda e=ents: from_machine(e)))
    bfrom_machine.pack(side=LEFT, padx=5, pady=5)
    blimit = Button(root, text='Set Player Limit',
                    command=(lambda e=ents: set_player_limit(e)))
    blimit.pack(side=LEFT, padx=5, pady=5)
    bquit = Button(root, text='Quit', command=root.quit)
    bquit.pack(side=LEFT, padx=5, pady=5)
    root.mainloop()
