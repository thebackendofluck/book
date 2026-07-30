#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""HSM Traffic Bot - simulates Cloudflare Worker traffic"""
import os
import json, time, urllib.request, base64, random, subprocess, sys
from datetime import datetime, timezone

API_KEY = os.environ["HSM_API_KEY"]  # export HSM_API_KEY before running
BASE = "http://127.0.0.1:8190"
MTLS_URL = "https://127.0.0.1:8443/hsm-api"
CERT = "/etc/nginx/ssl/client/hsm-client.crt"
KEY = "/etc/nginx/ssl/client/hsm-client.key"

def api(path, data=None):
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": API_KEY, "Content-Type": "application/json"})
    if data:
        req.data = json.dumps(data).encode()
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

def mtls(path, data=None):
    cmd = ["curl", "-4", "-sk", "--connect-timeout", "5", "--cert", CERT, "--key", KEY,
           "-H", "X-API-Key: " + API_KEY, "-H", "Content-Type: application/json"]
    if data:
        cmd += ["-d", json.dumps(data)]
    cmd.append(MTLS_URL + path)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(r.stdout) if r.stdout.strip() else {}

FIELDS = ["email", "phone", "ssn", "card_last4", "ip_address", "device_id"]
JWTS = ["login_token", "session_refresh", "withdrawal_auth", "kyc_verify"]

def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

interval = int(sys.argv[1]) if len(sys.argv) > 1 else 15
print("HSM Traffic Bot started (interval=%ds)" % interval, flush=True)

while True:
    try:
        ops = 0
        errs = 0
        # Encrypt + Decrypt cycle (GDPR field-level)
        for field in random.sample(FIELDS, random.randint(2, 4)):
            try:
                pt = base64.b64encode(("player_%s_%d" % (field, random.randint(1000, 9999))).encode()).decode()
                r = api("/hsm/encrypt", {"plaintext": pt})
                ops += 1
                r2 = api("/hsm/decrypt", {"ciphertext": r["ciphertext"]})
                ops += 1
            except Exception:
                errs += 1
        # Sign JWT tokens
        for payload in random.sample(JWTS, random.randint(1, 3)):
            try:
                inp = base64.b64encode(("%s_%s" % (payload, time.time())).encode()).decode()
                api("/hsm/sign", {"input": inp})
                ops += 1
            except Exception:
                errs += 1
        # Random bytes
        try:
            api("/hsm/random", {"bytes": 32})
            ops += 1
        except Exception:
            errs += 1
        # mTLS test
        mtls_ok = False
        try:
            pt = base64.b64encode(("mtls_%s" % time.time()).encode()).decode()
            r = mtls("/hsm/encrypt", {"plaintext": pt})
            if r.get("ciphertext"):
                mtls_ok = True
                ops += 1
        except Exception:
            errs += 1
        print("[%s] ops:%d errs:%d mtls:%s" % (ts(), ops, errs, "OK" if mtls_ok else "FAIL"), flush=True)
    except Exception as e:
        print("[%s] FATAL: %s" % (ts(), e), flush=True)
    time.sleep(interval)
