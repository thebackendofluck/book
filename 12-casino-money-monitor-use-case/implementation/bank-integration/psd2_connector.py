#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Money Monitor - PSD2 Open Banking Connector
====================================================
Chapter 5 Implementation: Checklist Item #2 (Part A)

Implements PSD2 (Payment Services Directive 2) Open Banking API integration
for real-time bank account balance and transaction retrieval.

Supports:
- Berlin Group NextGenPSD2 standard (used by most EU banks)
- UK Open Banking standard (OBIE)
- Strong Customer Authentication (SCA) flows
- Consent management and renewal
- Account Information Service Provider (AISP) role

PCI DSS Compliance Notes:
- Requirement 4.1: All bank API calls over TLS 1.2+
- Requirement 8.3: Strong authentication for all API access
- Requirement 10.5: Audit logs for all bank data access
- eIDAS: Qualified certificates (QWAC/QSeal) required

Dependencies:
    pip install httpx cryptography pydantic PyJWT
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pydantic import BaseModel, Field

logger = logging.getLogger("psd2_connector")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class PSD2Config(BaseModel):
    """Configuration for a PSD2 ASPSP (bank) connection."""
    aspsp_name: str                           # e.g., "Barclays", "Deutsche Bank"
    aspsp_id: str                             # BIC or institution ID
    api_base_url: str                         # e.g., "https://api.barclays.com/open-banking/v3.1"
    standard: str = "berlin_group"            # berlin_group | uk_obie
    client_id: str = ""
    client_secret: str = ""                   # stored in vault, never in config
    tpp_certificate_path: str = ""            # QWAC certificate (eIDAS)
    tpp_signing_key_path: str = ""            # QSeal private key
    tpp_signing_cert_path: str = ""           # QSeal certificate
    redirect_uri: str = ""
    consent_validity_days: int = 90           # max 90 days per PSD2
    sandbox_mode: bool = True


class ConsentStatus(str, Enum):
    RECEIVED = "received"
    VALID = "valid"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    TERMINATED = "terminated"


class PSD2Consent(BaseModel):
    """Tracks consent granted by the bank for account access."""
    consent_id: str
    aspsp_id: str
    status: ConsentStatus
    iban_list: list[str]
    valid_until: datetime
    frequency_per_day: int = 4
    recurring_indicator: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: Optional[datetime] = None


class BankAccount(BaseModel):
    """PSD2 account information response."""
    resource_id: str
    iban: str
    currency: str
    name: str
    bic: Optional[str] = None
    account_type: str = "current"
    balances: list[dict] = []


class BankBalance(BaseModel):
    """PSD2 balance detail."""
    balance_type: str          # expected, closingBooked, interimAvailable, etc.
    amount: str
    currency: str
    reference_date: Optional[str] = None
    last_change_datetime: Optional[str] = None


class BankTransaction(BaseModel):
    """PSD2 transaction detail."""
    transaction_id: str
    booking_date: str
    value_date: Optional[str] = None
    amount: str
    currency: str
    creditor_name: Optional[str] = None
    debtor_name: Optional[str] = None
    remittance_info: Optional[str] = None
    bank_transaction_code: Optional[str] = None
    status: str = "booked"     # booked | pending


# ---------------------------------------------------------------------------
# PSD2 Open Banking Connector
# ---------------------------------------------------------------------------

class PSD2Connector:
    """
    Connects to ASPSP (bank) APIs following PSD2 standards.

    Flow:
    1. Create consent -> bank returns consent_id
    2. Redirect PSU (Payment Service User) to bank for SCA
    3. After SCA, use consent to fetch accounts/balances/transactions
    4. Consent valid for up to 90 days with max 4 requests/day

    Casino use case: The casino operator (as a licensed AISP/PISP) uses
    PSD2 to get real-time visibility into settlement accounts at multiple banks.
    """

    def __init__(self, config: PSD2Config):
        self.config = config
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._consent: Optional[PSD2Consent] = None

        # TLS client with eIDAS certificate (QWAC)
        self._client = httpx.AsyncClient(
            base_url=config.api_base_url,
            cert=(config.tpp_certificate_path, config.tpp_signing_key_path) if config.tpp_certificate_path else None,
            timeout=30.0,
            verify=True,
        )

    # ---- Authentication ----

    async def authenticate(self) -> str:
        """
        Obtain access token via OAuth2 client credentials grant.
        Uses eIDAS QWAC certificate for mutual TLS.
        """
        if self._access_token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._access_token

        token_url = f"{self.config.api_base_url}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "scope": "accounts balances transactions",
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Request-ID": str(uuid4()),
        }

        response = await self._client.post(token_url, data=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        self._access_token = data["access_token"]
        self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))

        logger.info(f"Authenticated with {self.config.aspsp_name} (expires: {self._token_expiry})")
        return self._access_token

    # ---- Consent Management ----

    async def create_consent(self, ibans: list[str]) -> PSD2Consent:
        """
        Create an Account Information Consent with the ASPSP.

        Berlin Group example payload:
        {
            "access": {
                "accounts": [{"iban": "DE89370400440532013000"}],
                "balances": [{"iban": "DE89370400440532013000"}],
                "transactions": [{"iban": "DE89370400440532013000"}]
            },
            "recurringIndicator": true,
            "validUntil": "2026-06-08",
            "frequencyPerDay": 4,
            "combinedServiceIndicator": false
        }
        """
        token = await self.authenticate()
        valid_until = datetime.now(timezone.utc) + timedelta(days=self.config.consent_validity_days)

        if self.config.standard == "berlin_group":
            account_refs = [{"iban": iban} for iban in ibans]
            payload = {
                "access": {
                    "accounts": account_refs,
                    "balances": account_refs,
                    "transactions": account_refs,
                },
                "recurringIndicator": True,
                "validUntil": valid_until.strftime("%Y-%m-%d"),
                "frequencyPerDay": 4,
                "combinedServiceIndicator": False,
            }
            url = "/v1/consents"
        else:
            # UK Open Banking (OBIE) format
            permissions = [
                "ReadAccountsDetail",
                "ReadBalances",
                "ReadTransactionsDetail",
                "ReadTransactionsCredits",
                "ReadTransactionsDebits",
            ]
            payload = {
                "Data": {
                    "Permissions": permissions,
                    "ExpirationDateTime": valid_until.isoformat(),
                    "TransactionFromDateTime": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
                    "TransactionToDateTime": valid_until.isoformat(),
                },
                "Risk": {},
            }
            url = "/account-access-consents"

        headers = self._build_headers(token, payload)
        response = await self._client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        # Extract consent ID based on standard
        if self.config.standard == "berlin_group":
            consent_id = data.get("consentId", data.get("consent_id", ""))
            status_str = data.get("consentStatus", "received")
        else:
            consent_id = data.get("Data", {}).get("ConsentId", "")
            status_str = data.get("Data", {}).get("Status", "AwaitingAuthorisation")
            status_str = "received" if status_str == "AwaitingAuthorisation" else status_str.lower()

        self._consent = PSD2Consent(
            consent_id=consent_id,
            aspsp_id=self.config.aspsp_id,
            status=ConsentStatus(status_str),
            iban_list=ibans,
            valid_until=valid_until,
        )

        logger.info(f"Consent {consent_id} created at {self.config.aspsp_name}, status: {status_str}")
        return self._consent

    def get_sca_redirect_url(self, consent_id: str) -> str:
        """
        Generate the URL to redirect the PSU to the bank for Strong Customer Authentication.
        After SCA, the bank redirects back to our redirect_uri with the authorisation code.
        """
        if self.config.standard == "berlin_group":
            return (
                f"{self.config.api_base_url}/v1/consents/{consent_id}/authorisations"
                f"?redirect_uri={self.config.redirect_uri}"
            )
        else:
            return (
                f"{self.config.api_base_url}/authorize"
                f"?client_id={self.config.client_id}"
                f"&response_type=code"
                f"&scope=accounts"
                f"&consent_id={consent_id}"
                f"&redirect_uri={self.config.redirect_uri}"
            )

    # ---- Account Information ----

    async def get_accounts(self, consent_id: str) -> list[BankAccount]:
        """
        Retrieve list of accounts covered by the consent.
        Max 4 calls per day per PSD2 regulation.
        """
        token = await self.authenticate()
        headers = self._build_headers(token)
        headers["Consent-ID"] = consent_id

        if self.config.standard == "berlin_group":
            url = "/v1/accounts"
        else:
            url = "/accounts"

        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        accounts = []
        if self.config.standard == "berlin_group":
            for acct in data.get("accounts", []):
                accounts.append(BankAccount(
                    resource_id=acct.get("resourceId", ""),
                    iban=acct.get("iban", ""),
                    currency=acct.get("currency", "EUR"),
                    name=acct.get("name", acct.get("product", "")),
                    bic=acct.get("bic"),
                ))
        else:
            for acct in data.get("Data", {}).get("Account", []):
                iban = ""
                for scheme in acct.get("Account", []):
                    if scheme.get("SchemeName") == "UK.OBIE.IBAN":
                        iban = scheme.get("Identification", "")
                accounts.append(BankAccount(
                    resource_id=acct.get("AccountId", ""),
                    iban=iban,
                    currency=acct.get("Currency", "GBP"),
                    name=acct.get("Nickname", ""),
                ))

        logger.info(f"Retrieved {len(accounts)} accounts from {self.config.aspsp_name}")
        return accounts

    async def get_balances(self, consent_id: str, account_id: str) -> list[BankBalance]:
        """Retrieve balances for a specific account."""
        token = await self.authenticate()
        headers = self._build_headers(token)
        headers["Consent-ID"] = consent_id

        if self.config.standard == "berlin_group":
            url = f"/v1/accounts/{account_id}/balances"
        else:
            url = f"/accounts/{account_id}/balances"

        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        balances = []
        if self.config.standard == "berlin_group":
            for bal in data.get("balances", []):
                balances.append(BankBalance(
                    balance_type=bal.get("balanceType", ""),
                    amount=bal.get("balanceAmount", {}).get("amount", "0"),
                    currency=bal.get("balanceAmount", {}).get("currency", "EUR"),
                    reference_date=bal.get("referenceDate"),
                    last_change_datetime=bal.get("lastChangeDateTime"),
                ))
        else:
            for bal in data.get("Data", {}).get("Balance", []):
                balances.append(BankBalance(
                    balance_type=bal.get("Type", ""),
                    amount=bal.get("Amount", {}).get("Amount", "0"),
                    currency=bal.get("Amount", {}).get("Currency", "GBP"),
                    reference_date=bal.get("DateTime"),
                ))

        return balances

    async def get_transactions(
        self,
        consent_id: str,
        account_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[BankTransaction]:
        """
        Retrieve transactions for settlement reconciliation.
        Used by the daily reconciliation engine to match deposits/withdrawals.
        """
        token = await self.authenticate()
        headers = self._build_headers(token)
        headers["Consent-ID"] = consent_id

        params = {}
        if self.config.standard == "berlin_group":
            url = f"/v1/accounts/{account_id}/transactions"
            if date_from:
                params["dateFrom"] = date_from
            if date_to:
                params["dateTo"] = date_to
            params["bookingStatus"] = "both"
        else:
            url = f"/accounts/{account_id}/transactions"
            if date_from:
                params["fromBookingDateTime"] = date_from
            if date_to:
                params["toBookingDateTime"] = date_to

        response = await self._client.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        transactions = []
        if self.config.standard == "berlin_group":
            txn_data = data.get("transactions", {})
            for txn in txn_data.get("booked", []):
                transactions.append(BankTransaction(
                    transaction_id=txn.get("transactionId", str(uuid4())),
                    booking_date=txn.get("bookingDate", ""),
                    value_date=txn.get("valueDate"),
                    amount=txn.get("transactionAmount", {}).get("amount", "0"),
                    currency=txn.get("transactionAmount", {}).get("currency", "EUR"),
                    creditor_name=txn.get("creditorName"),
                    debtor_name=txn.get("debtorName"),
                    remittance_info=txn.get("remittanceInformationUnstructured"),
                    status="booked",
                ))
            for txn in txn_data.get("pending", []):
                transactions.append(BankTransaction(
                    transaction_id=txn.get("transactionId", str(uuid4())),
                    booking_date=txn.get("bookingDate", ""),
                    amount=txn.get("transactionAmount", {}).get("amount", "0"),
                    currency=txn.get("transactionAmount", {}).get("currency", "EUR"),
                    remittance_info=txn.get("remittanceInformationUnstructured"),
                    status="pending",
                ))
        else:
            for txn in data.get("Data", {}).get("Transaction", []):
                transactions.append(BankTransaction(
                    transaction_id=txn.get("TransactionId", str(uuid4())),
                    booking_date=txn.get("BookingDateTime", ""),
                    value_date=txn.get("ValueDateTime"),
                    amount=txn.get("Amount", {}).get("Amount", "0"),
                    currency=txn.get("Amount", {}).get("Currency", "GBP"),
                    creditor_name=txn.get("CreditorAccount", {}).get("Name"),
                    debtor_name=txn.get("DebtorAccount", {}).get("Name"),
                    remittance_info=txn.get("TransactionInformation"),
                    status=txn.get("Status", "Booked").lower(),
                ))

        logger.info(f"Retrieved {len(transactions)} transactions from {self.config.aspsp_name}/{account_id}")
        return transactions

    # ---- Internal Helpers ----

    def _build_headers(self, token: str, payload: Optional[dict] = None) -> dict:
        """Build PSD2-compliant request headers."""
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Request-ID": str(uuid4()),
            "PSU-IP-Address": "10.0.0.1",  # server-to-server, use operator's IP
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Sign the request with QSeal certificate (eIDAS signature)
        if payload and self.config.tpp_signing_key_path:
            digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
            headers["Digest"] = f"SHA-256={digest}"
            headers["Signature"] = self._create_signature(headers, digest)
            headers["TPP-Signature-Certificate"] = self.config.tpp_signing_cert_path

        return headers

    def _create_signature(self, headers: dict, digest: str) -> str:
        """
        Create HTTP Signature per PSD2 requirements.
        In production, sign with QSeal private key.
        """
        # Simplified - production would use the actual QSeal key
        sign_string = f'digest: SHA-256={digest}\nx-request-id: {headers["X-Request-ID"]}'
        signature_b64 = hashlib.sha256(sign_string.encode()).hexdigest()

        return (
            f'keyId="{self.config.client_id}",'
            f'algorithm="rsa-sha256",'
            f'headers="digest x-request-id",'
            f'signature="{signature_b64}"'
        )

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Pre-configured Bank Connectors for Common Casino Banking Partners
# ---------------------------------------------------------------------------

def create_barclays_connector(client_id: str, sandbox: bool = True) -> PSD2Connector:
    """Barclays UK - commonly used by UKGC-licensed operators."""
    base = "https://sandbox.api.barclays.com/open-banking/v3.1" if sandbox else "https://api.barclays.com/open-banking/v3.1"
    return PSD2Connector(PSD2Config(
        aspsp_name="Barclays",
        aspsp_id="BARCGB22",
        api_base_url=base,
        standard="uk_obie",
        client_id=client_id,
        sandbox_mode=sandbox,
    ))


def create_bov_connector(client_id: str, sandbox: bool = True) -> PSD2Connector:
    """Bank of Valletta - commonly used by MGA-licensed operators."""
    base = "https://sandbox.bov.com/psd2/v1" if sandbox else "https://api.bov.com/psd2/v1"
    return PSD2Connector(PSD2Config(
        aspsp_name="Bank of Valletta",
        aspsp_id="VALLMTMT",
        api_base_url=base,
        standard="berlin_group",
        client_id=client_id,
        sandbox_mode=sandbox,
    ))


def create_deutsche_bank_connector(client_id: str, sandbox: bool = True) -> PSD2Connector:
    """Deutsche Bank - used by operators with German market presence."""
    base = "https://simulator-api.db.com/gw/dbapi/paymentInitiation/payments/v1" if sandbox else "https://api.db.com/gw/dbapi/v1"
    return PSD2Connector(PSD2Config(
        aspsp_name="Deutsche Bank",
        aspsp_id="DEUTDEFF",
        api_base_url=base,
        standard="berlin_group",
        client_id=client_id,
        sandbox_mode=sandbox,
    ))


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

async def main():
    """
    Example: Operator fetches real-time balances from Barclays
    to feed into the Cash Position Dashboard.
    """
    connector = create_barclays_connector(client_id="casino-aisp-client-001")

    try:
        # Step 1: Create consent for operator's settlement accounts
        consent = await connector.create_consent(ibans=[
            "GB82BARC20040455667788",  # Player Funds account
            "GB82BARC20040455667789",  # Operating account
        ])
        print(f"Consent created: {consent.consent_id}")

        # Step 2: In production, redirect finance director to bank for SCA
        sca_url = connector.get_sca_redirect_url(consent.consent_id)
        print(f"SCA URL: {sca_url}")

        # Step 3: After SCA approval, fetch accounts
        accounts = await connector.get_accounts(consent.consent_id)
        for acct in accounts:
            print(f"Account: {acct.iban} ({acct.currency}) - {acct.name}")

            # Step 4: Get real-time balances
            balances = await connector.get_balances(consent.consent_id, acct.resource_id)
            for bal in balances:
                print(f"  {bal.balance_type}: {bal.amount} {bal.currency}")

    except httpx.HTTPStatusError as e:
        print(f"Bank API error: {e.response.status_code} - {e.response.text}")
    finally:
        await connector.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
