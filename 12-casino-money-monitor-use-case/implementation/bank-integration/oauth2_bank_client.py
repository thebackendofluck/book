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
Casino Money Monitor - OAuth2 Bank API Client
===============================================
Chapter 5 Implementation: Checklist Item #2 (Part B)

Generic OAuth2 + certificate-based bank API client supporting:
- OAuth2 Authorization Code flow (for user-delegated access)
- OAuth2 Client Credentials flow (for server-to-server)
- Mutual TLS (mTLS) with client certificates
- Token refresh and rotation
- Request signing and audit logging

Designed for banks that do NOT support PSD2 (non-EU jurisdictions)
or legacy banking APIs that use proprietary OAuth2 implementations.

PCI DSS Compliance Notes:
- Requirement 4.1: Mutual TLS with bank endpoints
- Requirement 8.2.1: Unique credential per bank integration
- Requirement 3.6: Cryptographic key management for signing keys
- Requirement 10.2: All bank API calls are logged

Dependencies:
    pip install httpx cryptography pydantic python-jose
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("oauth2_bank_client")

# ---------------------------------------------------------------------------
# Configuration Models
# ---------------------------------------------------------------------------

class AuthMethod(str, Enum):
    CLIENT_CREDENTIALS = "client_credentials"
    AUTHORIZATION_CODE = "authorization_code"
    MUTUAL_TLS = "mutual_tls"
    CERTIFICATE_BOUND = "certificate_bound"


class BankAPIConfig(BaseModel):
    """Configuration for a bank API connection."""
    bank_name: str
    bank_id: str
    base_url: str
    auth_method: AuthMethod = AuthMethod.CLIENT_CREDENTIALS

    # OAuth2
    token_endpoint: str = ""
    authorize_endpoint: str = ""
    client_id: str = ""
    client_secret: str = ""           # from vault
    scopes: list[str] = ["accounts", "balances", "transactions"]
    redirect_uri: str = ""

    # Certificate-based auth
    client_cert_path: str = ""        # .pem or .p12
    client_key_path: str = ""         # private key
    ca_bundle_path: str = ""          # bank's CA certificate

    # API-specific
    api_version: str = "v1"
    rate_limit_per_minute: int = 60
    timeout_seconds: int = 30
    retry_count: int = 3
    retry_backoff_seconds: float = 1.0


class TokenInfo(BaseModel):
    """OAuth2 token storage."""
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    refresh_token: Optional[str] = None
    scope: str = ""
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------

class BankAPIAuditLog:
    """
    Audit logger for all bank API interactions.
    PCI DSS Requirement 10.2: Log all access to cardholder data environment.
    """

    def __init__(self, bank_id: str):
        self.bank_id = bank_id
        self._audit_logger = logging.getLogger(f"audit.bank_api.{bank_id}")
        handler = logging.FileHandler(f"/var/log/casino/bank_api_{bank_id}.audit.log")
        handler.setFormatter(logging.Formatter(
            '{"timestamp":"%(asctime)s","level":"%(levelname)s","bank":"%(name)s","event":%(message)s}'
        ))
        self._audit_logger.addHandler(handler)
        self._audit_logger.setLevel(logging.INFO)

    def log_request(self, method: str, url: str, request_id: str, user: str = "system"):
        self._audit_logger.info(json.dumps({
            "action": "api_request",
            "method": method,
            "url": url,
            "request_id": request_id,
            "user": user,
        }))

    def log_response(self, request_id: str, status_code: int, duration_ms: float):
        self._audit_logger.info(json.dumps({
            "action": "api_response",
            "request_id": request_id,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
        }))

    def log_token_event(self, event: str, expires_at: str):
        self._audit_logger.info(json.dumps({
            "action": f"token_{event}",
            "expires_at": expires_at,
        }))


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple sliding-window rate limiter for bank API calls."""

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []

    def acquire(self) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        if len(self._timestamps) >= self.max_requests:
            return False

        self._timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        now = time.time()
        cutoff = now - self.window_seconds
        active = [t for t in self._timestamps if t > cutoff]
        return max(0, self.max_requests - len(active))


# ---------------------------------------------------------------------------
# OAuth2 Bank Client
# ---------------------------------------------------------------------------

class OAuth2BankClient:
    """
    Production-grade OAuth2 bank API client with automatic token management,
    mutual TLS, rate limiting, and audit logging.

    Typical casino operator scenario:
    - Multiple bank accounts across jurisdictions
    - Automated balance polling every 5-15 minutes
    - Transaction retrieval for reconciliation
    - Wire transfer initiation for large withdrawals
    """

    def __init__(self, config: BankAPIConfig):
        self.config = config
        self._token: Optional[TokenInfo] = None
        self._rate_limiter = RateLimiter(config.rate_limit_per_minute)
        self._audit = BankAPIAuditLog(config.bank_id)

        # Build HTTP client with certificate config
        client_kwargs: dict[str, Any] = {
            "base_url": config.base_url,
            "timeout": config.timeout_seconds,
            "verify": config.ca_bundle_path or True,
        }

        # Mutual TLS: present client certificate to bank
        if config.client_cert_path and config.client_key_path:
            client_kwargs["cert"] = (config.client_cert_path, config.client_key_path)
            logger.info(f"mTLS enabled for {config.bank_name}")

        self._client = httpx.AsyncClient(**client_kwargs)

    # ---- Token Management ----

    async def _obtain_token_client_credentials(self) -> TokenInfo:
        """OAuth2 Client Credentials flow - server-to-server."""
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "scope": " ".join(self.config.scopes),
        }

        response = await self._client.post(
            self.config.token_endpoint,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return self._parse_token_response(response.json())

    async def _obtain_token_auth_code(self, auth_code: str) -> TokenInfo:
        """OAuth2 Authorization Code flow - user-delegated."""
        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
        }

        response = await self._client.post(
            self.config.token_endpoint,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return self._parse_token_response(response.json())

    async def _refresh_token(self) -> TokenInfo:
        """Refresh an expired access token."""
        if not self._token or not self._token.refresh_token:
            raise RuntimeError("No refresh token available; re-authenticate required")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }

        response = await self._client.post(
            self.config.token_endpoint,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token = self._parse_token_response(response.json())
        self._audit.log_token_event("refreshed", token.expires_at.isoformat())
        return token

    def _parse_token_response(self, data: dict) -> TokenInfo:
        expires_in = data.get("expires_in", 3600)
        return TokenInfo(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope", ""),
        )

    async def ensure_authenticated(self) -> str:
        """Ensure we have a valid access token, refreshing or re-obtaining as needed."""
        now = datetime.now(timezone.utc)

        if self._token:
            # Token still valid (with 60s buffer)
            if self._token.expires_at > now + timedelta(seconds=60):
                return self._token.access_token

            # Try refresh
            if self._token.refresh_token:
                try:
                    self._token = await self._refresh_token()
                    return self._token.access_token
                except Exception as e:
                    logger.warning(f"Token refresh failed for {self.config.bank_name}: {e}")

        # Obtain new token
        if self.config.auth_method in (AuthMethod.CLIENT_CREDENTIALS, AuthMethod.MUTUAL_TLS):
            self._token = await self._obtain_token_client_credentials()
        else:
            raise RuntimeError(
                f"Auth code flow requires user interaction. "
                f"Call get_authorization_url() and then exchange_auth_code()"
            )

        self._audit.log_token_event("obtained", self._token.expires_at.isoformat())
        return self._token.access_token

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Generate authorization URL for user-delegated flows."""
        state = state or str(uuid4())
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.config.authorize_endpoint}?{query}"

    async def exchange_auth_code(self, code: str) -> TokenInfo:
        """Exchange authorization code for tokens."""
        self._token = await self._obtain_token_auth_code(code)
        self._audit.log_token_event("obtained_via_auth_code", self._token.expires_at.isoformat())
        return self._token

    # ---- API Methods ----

    async def request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        user: str = "system",
    ) -> dict:
        """
        Make an authenticated, rate-limited, audited API request.
        Includes automatic retry with exponential backoff.
        """
        if not self._rate_limiter.acquire():
            raise RuntimeError(
                f"Rate limit exceeded for {self.config.bank_name} "
                f"({self.config.rate_limit_per_minute}/min)"
            )

        token = await self.ensure_authenticated()
        request_id = str(uuid4())

        headers = {
            "Authorization": f"Bearer {token}",
            "X-Request-ID": request_id,
            "Accept": "application/json",
        }

        self._audit.log_request(method, path, request_id, user)
        start_time = time.time()

        last_error = None
        for attempt in range(self.config.retry_count):
            try:
                response = await self._client.request(
                    method=method,
                    url=path,
                    json=data,
                    params=params,
                    headers=headers,
                )

                duration_ms = (time.time() - start_time) * 1000
                self._audit.log_response(request_id, response.status_code, duration_ms)

                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429:
                    # Rate limited by bank - back off
                    wait = self.config.retry_backoff_seconds * (2 ** attempt)
                    logger.warning(f"Bank rate limit hit, waiting {wait}s (attempt {attempt + 1})")
                    import asyncio
                    await asyncio.sleep(wait)
                elif e.response.status_code == 401:
                    # Token expired during request - refresh
                    self._token = None
                    token = await self.ensure_authenticated()
                    headers["Authorization"] = f"Bearer {token}"
                elif e.response.status_code >= 500:
                    # Server error - retry
                    wait = self.config.retry_backoff_seconds * (2 ** attempt)
                    logger.warning(f"Bank server error {e.response.status_code}, retry in {wait}s")
                    import asyncio
                    await asyncio.sleep(wait)
                else:
                    raise

            except httpx.TimeoutException:
                last_error = httpx.TimeoutException(f"Timeout on attempt {attempt + 1}")
                logger.warning(f"Bank API timeout (attempt {attempt + 1}/{self.config.retry_count})")

        raise RuntimeError(f"Bank API request failed after {self.config.retry_count} attempts: {last_error}")

    async def get_accounts(self) -> list[dict]:
        """Get list of bank accounts."""
        data = await self.request("GET", f"/{self.config.api_version}/accounts")
        return data.get("accounts", data.get("data", []))

    async def get_balance(self, account_id: str) -> dict:
        """Get balance for a specific account."""
        return await self.request("GET", f"/{self.config.api_version}/accounts/{account_id}/balance")

    async def get_transactions(
        self,
        account_id: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[dict]:
        """Get transactions for reconciliation."""
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = await self.request(
            "GET",
            f"/{self.config.api_version}/accounts/{account_id}/transactions",
            params=params,
        )
        return data.get("transactions", data.get("data", []))

    async def get_statement(self, account_id: str, statement_date: str) -> dict:
        """Get bank statement for a specific date (used in daily reconciliation)."""
        return await self.request(
            "GET",
            f"/{self.config.api_version}/accounts/{account_id}/statements/{statement_date}",
        )

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Pre-configured Bank Clients
# ---------------------------------------------------------------------------

def create_hsbc_client(client_id: str, client_secret: str, cert_path: str = "", key_path: str = "") -> OAuth2BankClient:
    """HSBC UK - common operating account bank for UK operators."""
    return OAuth2BankClient(BankAPIConfig(
        bank_name="HSBC UK",
        bank_id="HSBCGB2L",
        base_url="https://api.hsbc.com/open-banking",
        auth_method=AuthMethod.CERTIFICATE_BOUND,
        token_endpoint="https://api.hsbc.com/oauth2/token",
        client_id=client_id,
        client_secret=client_secret,
        client_cert_path=cert_path,
        client_key_path=key_path,
        scopes=["accounts", "balances", "transactions"],
        rate_limit_per_minute=30,
    ))


def create_mcb_curacao_client(client_id: str, client_secret: str) -> OAuth2BankClient:
    """MCB Curacao - used by Curacao-licensed operators."""
    return OAuth2BankClient(BankAPIConfig(
        bank_name="MCB Curacao",
        bank_id="MCBKCWCU",
        base_url="https://api.mcb-bank.com/business",
        auth_method=AuthMethod.CLIENT_CREDENTIALS,
        token_endpoint="https://api.mcb-bank.com/oauth/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["read_accounts", "read_balances"],
        rate_limit_per_minute=20,
    ))


def create_nab_australia_client(client_id: str, client_secret: str) -> OAuth2BankClient:
    """National Australia Bank - for ACMA-regulated operators."""
    return OAuth2BankClient(BankAPIConfig(
        bank_name="NAB Australia",
        bank_id="NATAAU33",
        base_url="https://openbank.api.nab.com.au/cds-au/v1",
        auth_method=AuthMethod.CLIENT_CREDENTIALS,
        token_endpoint="https://openbank.api.nab.com.au/oauth2/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["bank:accounts.basic:read", "bank:transactions:read"],
        api_version="v1",
        rate_limit_per_minute=60,
    ))


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

async def main():
    """Example: Poll balances from multiple banks for cash dashboard."""

    banks = [
        create_hsbc_client("casino-client-001", "${HSBC_SECRET}"),
        create_mcb_curacao_client("casino-client-002", "${MCB_SECRET}"),
    ]

    for client in banks:
        try:
            accounts = await client.get_accounts()
            print(f"\n{client.config.bank_name} - {len(accounts)} accounts:")

            for acct in accounts:
                acct_id = acct.get("accountId", acct.get("id", ""))
                balance = await client.get_balance(acct_id)
                print(f"  {acct_id}: {json.dumps(balance, indent=2)}")

        except Exception as e:
            logger.error(f"Error polling {client.config.bank_name}: {e}")
        finally:
            await client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
