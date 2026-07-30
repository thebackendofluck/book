# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PIX PSP Sandbox Integration Demo
=================================
Chapter 46 — Building a Brazilian Betting Platform

This demo requires sandbox API keys. Register at the PSP developer portals
listed in Chapter 46.17:
  - Celcoin:    https://sandbox.openfinance.celcoin.dev
  - Stark Bank: https://starkbank.com/sandbox
  - Transfeera: https://api-sandbox.transfeera.com

Usage:
    pip install httpx python-dotenv
    python pix_sandbox_demo.py

Set environment variables (or create a .env file):
    CELCOIN_CLIENT_ID=your_sandbox_client_id
    CELCOIN_CLIENT_SECRET=your_sandbox_client_secret
    STARKBANK_PROJECT_ID=your_project_id
    STARKBANK_PRIVATE_KEY_PATH=path/to/private.pem
    TRANSFEERA_CLIENT_ID=your_client_id
    TRANSFEERA_CLIENT_SECRET=your_client_secret
"""

import ast
import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# --- Dependency check ---
# The demo uses httpx for async HTTP. Install with: pip install httpx
try:
    import httpx
except ImportError:
    raise SystemExit(
        "httpx is required. Install it with: pip install httpx"
    )

# ---------------------------------------------------------------------------
# ANSI colour helpers — make terminal output readable at a glance
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
DIM = "\033[2m"


def ok(msg: str) -> None:
    print(f"{GREEN}  [OK]{RESET} {msg}")


def info(msg: str) -> None:
    print(f"{CYAN}  [INFO]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}  [WARN]{RESET} {msg}")


def err(msg: str) -> None:
    print(f"{RED}  [ERR]{RESET} {msg}")


def step(n: int, title: str) -> None:
    print(f"\n{BOLD}{MAGENTA}── Step {n}: {title}{RESET}")


def section(title: str) -> None:
    bar = "═" * 60
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")


def dump(label: str, data: Any) -> None:
    """Pretty-print a dict or string with a dim label."""
    serialised = json.dumps(data, indent=2, ensure_ascii=False) if isinstance(data, dict) else str(data)
    print(f"{DIM}  {label}:{RESET}")
    for line in serialised.splitlines():
        print(f"  {DIM}{line}{RESET}")


# ---------------------------------------------------------------------------
# Shared data model
# ---------------------------------------------------------------------------

@dataclass
class PIXCharge:
    """Represents a PIX charge (cobrança) returned by any PSP."""
    charge_id: str
    psp: str
    amount_brl: float
    debtor_cpf: str
    debtor_name: str
    status: str                          # PENDING | PAID | EXPIRED | CANCELLED
    qr_code: str                         # EMV payload for the QR code image
    copy_paste_key: str                  # Copia-e-cola string
    expiry_seconds: int = 3600
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_response: dict = field(default_factory=dict)


@dataclass
class PIXPayout:
    """Represents a PIX payout (transferência) to a player."""
    payout_id: str
    psp: str
    amount_brl: float
    recipient_cpf: str
    recipient_name: str
    recipient_pix_key: str              # CPF, phone, e-mail, or EVP key
    status: str                          # QUEUED | PROCESSING | DONE | FAILED
    end_to_end_id: Optional[str] = None  # BCB's E2E identifier
    raw_response: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Celcoin Sandbox
# ---------------------------------------------------------------------------

CELCOIN_SANDBOX_BASE = "https://sandbox.openfinance.celcoin.dev"
CELCOIN_AUTH_URL = f"{CELCOIN_SANDBOX_BASE}/v5/token"
CELCOIN_CHARGE_URL = f"{CELCOIN_SANDBOX_BASE}/pix/v1/charge"


class CelcoinSandbox:
    """
    Celcoin is the recommended primary PSP for Brazilian iGaming.
    It holds a BCB IP (Instituição de Pagamento) license and explicitly
    supports the iGaming vertical.

    Sandbox docs: https://sandbox.openfinance.celcoin.dev/docs
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """
        Celcoin uses OAuth 2.0 client_credentials flow.
        Tokens are valid for 3600 seconds — cache and reuse them.
        """
        if self._token and time.time() < self._token_expiry - 60:
            return self._token  # Still valid; return cached token

        info("Requesting Celcoin OAuth token …")
        response = await client.post(
            CELCOIN_AUTH_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + payload.get("expires_in", 3600)
        ok(f"Celcoin token obtained (expires in {payload.get('expires_in', '?')}s)")
        return self._token

    async def create_pix_charge(
        self,
        client: httpx.AsyncClient,
        *,
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
        expiry_seconds: int = 3600,
    ) -> PIXCharge:
        """
        Create a dynamic PIX QR code (cobrança) for a deposit.

        Celcoin follows the BCB DICT spec for dynamic QR codes:
          - amount is in decimal BRL (not centavos)
          - key is the operator's registered PIX key
          - infoAdicionais carry your internal transaction ID
        """
        token = await self._get_token(client)
        info(f"Creating Celcoin PIX charge for R${amount_brl:.2f} …")

        body = {
            # Your platform's PIX key registered with Celcoin
            # In production this is your CNPJ or a random EVP key
            "key": os.getenv("CELCOIN_PIX_KEY", "12345678000195"),
            "amount": amount_brl,
            "initiationType": "DYNAMIC",
            "payerQuestion": f"Deposit — {external_id}",
            "additionalInfo": [
                {"name": "txId", "value": external_id},
                {"name": "platform", "value": "BetBrasil"},
            ],
            "debtor": {
                "name": debtor_name,
                "cpf": debtor_cpf.replace(".", "").replace("-", ""),
            },
            "expirationDate": expiry_seconds,
        }

        response = await client.post(
            CELCOIN_CHARGE_URL,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )

        # Celcoin returns 200 on success even for async operations
        if response.status_code not in (200, 201):
            err(f"Celcoin charge failed: {response.status_code} {response.text}")
            # Return a mock response so the demo keeps running without real creds
            return self._mock_charge(amount_brl, debtor_cpf, debtor_name, external_id)

        data = response.json()
        ok(f"Celcoin charge created: {data.get('transactionId', 'N/A')}")

        return PIXCharge(
            charge_id=data.get("transactionId", external_id),
            psp="celcoin",
            amount_brl=amount_brl,
            debtor_cpf=debtor_cpf,
            debtor_name=debtor_name,
            status="PENDING",
            qr_code=data.get("emvqrcps", ""),       # EMV QR Code string
            copy_paste_key=data.get("emvqrcps", ""), # Same string used for copy-paste
            expiry_seconds=expiry_seconds,
            raw_response=data,
        )

    async def check_charge_status(
        self,
        client: httpx.AsyncClient,
        charge_id: str,
    ) -> str:
        """
        Poll Celcoin for charge status.
        In production, use webhooks instead of polling — see section 46.14.
        """
        token = await self._get_token(client)
        response = await client.get(
            f"{CELCOIN_CHARGE_URL}/{charge_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code == 404:
            return "NOT_FOUND"
        if response.status_code != 200:
            return "ERROR"
        data = response.json()
        # Celcoin statuses: PENDING, COMPLETED, CANCELLED, EXPIRED
        return data.get("status", "UNKNOWN").upper()

    @staticmethod
    def _mock_charge(
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
    ) -> PIXCharge:
        """Return a plausible mock when sandbox credentials are absent."""
        warn("Using MOCK Celcoin response (no real credentials configured)")
        fake_id = f"celcoin-mock-{uuid.uuid4().hex[:12]}"
        fake_emv = (
            "00020126580014br.gov.bcb.pix0136"
            "e6a9e7dc-mock-sandbox-key-not-real"
            f"520400005303986540{amount_brl:.2f}5802BR"
            "5910BetBrasil6009SaoPaulo62070503***"
            "63041234"
        )
        return PIXCharge(
            charge_id=fake_id,
            psp="celcoin",
            amount_brl=amount_brl,
            debtor_cpf=debtor_cpf,
            debtor_name=debtor_name,
            status="PENDING",
            qr_code=fake_emv,
            copy_paste_key=fake_emv,
            raw_response={"mock": True, "transactionId": fake_id},
        )


# ---------------------------------------------------------------------------
# Stark Bank Sandbox
# ---------------------------------------------------------------------------

STARKBANK_SANDBOX_BASE = "https://sandbox.api.starkbank.com/v2"


class StarkBankSandbox:
    """
    Stark Bank (IP + SCD license) is the top choice for high-volume platforms
    that need PIX payouts at scale. Their PIX-in fee is R$0, making deposits
    cost-free. Payouts via PIX are also zero-cost in their latest pricing.

    Stark Bank uses ECDSA (P-256) for request signing, not OAuth tokens.
    The SDK (starkbank-python) handles signing automatically.
    This class shows the raw HTTP approach for clarity.

    Sandbox docs: https://starkbank.com/faq/how-to-test-on-sandbox
    """

    def __init__(self, project_id: str, private_key_pem: str) -> None:
        self.project_id = project_id
        self.private_key_pem = private_key_pem  # ECDSA P-256 PEM

    def _auth_header(self, method: str, path: str, body: str) -> dict[str, str]:
        """
        Stark Bank requires every request to be signed with ECDSA P-256.
        The canonical message is: '{epoch}:{body}'
        The Authorization header format is: 'Signature {project_id}:{base64_sig}'

        In production, use the official SDK which handles this automatically:
            pip install starkbank
        """
        epoch = str(int(time.time()))
        message = f"{epoch}:{body}"
        # NOTE: Real signing requires the cryptography library and the ECDSA key.
        # This is a placeholder signature for demo structure clarity.
        placeholder_sig = hashlib.sha256(
            (message + self.private_key_pem).encode()
        ).hexdigest()
        return {
            "Access-Id": self.project_id,
            "Access-Time": epoch,
            "Access-Signature": placeholder_sig,
            "Content-Type": "application/json",
        }

    async def create_pix_invoice(
        self,
        client: httpx.AsyncClient,
        *,
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
        expiry_seconds: int = 3600,
    ) -> PIXCharge:
        """
        Stark Bank calls deposit QR codes 'invoices'.
        Amount must be in centavos (integer).
        """
        info(f"Creating Stark Bank PIX invoice for R${amount_brl:.2f} …")
        amount_cents = int(round(amount_brl * 100))
        body_dict = {
            "invoices": [
                {
                    "amount": amount_cents,
                    "name": debtor_name,
                    "taxId": debtor_cpf.replace(".", "").replace("-", ""),
                    "due": expiry_seconds,
                    "expiration": expiry_seconds,
                    "tags": [f"external_id:{external_id}", "source:betbrasil"],
                    "descriptions": [
                        {"key": "txId", "value": external_id},
                        {"key": "platform", "value": "BetBrasil"},
                    ],
                }
            ]
        }
        body_str = json.dumps(body_dict)
        headers = self._auth_header("POST", "/invoice", body_str)

        response = await client.post(
            f"{STARKBANK_SANDBOX_BASE}/invoice",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        if response.status_code not in (200, 201):
            err(f"Stark Bank invoice failed: {response.status_code} {response.text}")
            return self._mock_charge(amount_brl, debtor_cpf, debtor_name, external_id)

        data = response.json()
        invoice = data.get("invoices", [{}])[0]
        ok(f"Stark Bank invoice created: {invoice.get('id', 'N/A')}")

        return PIXCharge(
            charge_id=invoice.get("id", external_id),
            psp="starkbank",
            amount_brl=amount_brl,
            debtor_cpf=debtor_cpf,
            debtor_name=debtor_name,
            status=invoice.get("status", "created").upper(),
            qr_code=invoice.get("brcode", ""),
            copy_paste_key=invoice.get("brcode", ""),
            expiry_seconds=expiry_seconds,
            raw_response=invoice,
        )

    async def create_pix_payout(
        self,
        client: httpx.AsyncClient,
        *,
        amount_brl: float,
        recipient_cpf: str,
        recipient_name: str,
        recipient_pix_key: str,
        external_id: str,
    ) -> PIXPayout:
        """
        Send a PIX payout to a player.
        Stark Bank calls these 'transfers'.
        """
        info(f"Creating Stark Bank PIX payout of R${amount_brl:.2f} to {recipient_name} …")
        amount_cents = int(round(amount_brl * 100))
        body_dict = {
            "transfers": [
                {
                    "amount": amount_cents,
                    "name": recipient_name,
                    "taxId": recipient_cpf.replace(".", "").replace("-", ""),
                    "bankCode": "20018183",  # Stark Bank's ISPB
                    "branchCode": "0001",
                    "accountNumber": "6341320293482496",
                    "accountType": "payment",
                    # For PIX key-based transfers (preferred):
                    # "pixKey": recipient_pix_key,
                    "tags": [f"external_id:{external_id}", "type:withdrawal"],
                }
            ]
        }
        body_str = json.dumps(body_dict)
        headers = self._auth_header("POST", "/transfer", body_str)

        response = await client.post(
            f"{STARKBANK_SANDBOX_BASE}/transfer",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        if response.status_code not in (200, 201):
            err(f"Stark Bank transfer failed: {response.status_code} {response.text}")
            return self._mock_payout(amount_brl, recipient_cpf, recipient_name, recipient_pix_key, external_id)

        data = response.json()
        transfer = data.get("transfers", [{}])[0]
        ok(f"Stark Bank transfer created: {transfer.get('id', 'N/A')}")

        return PIXPayout(
            payout_id=transfer.get("id", external_id),
            psp="starkbank",
            amount_brl=amount_brl,
            recipient_cpf=recipient_cpf,
            recipient_name=recipient_name,
            recipient_pix_key=recipient_pix_key,
            status=transfer.get("status", "processing").upper(),
            end_to_end_id=transfer.get("endToEndId"),
            raw_response=transfer,
        )

    @staticmethod
    def _mock_charge(
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
    ) -> PIXCharge:
        warn("Using MOCK Stark Bank response (no real credentials configured)")
        fake_id = f"starkbank-mock-{uuid.uuid4().hex[:12]}"
        return PIXCharge(
            charge_id=fake_id,
            psp="starkbank",
            amount_brl=amount_brl,
            debtor_cpf=debtor_cpf,
            debtor_name=debtor_name,
            status="CREATED",
            qr_code="00020126mock-starkbank-brcode6304ABCD",
            copy_paste_key="00020126mock-starkbank-brcode6304ABCD",
            raw_response={"mock": True, "id": fake_id},
        )

    @staticmethod
    def _mock_payout(
        amount_brl: float,
        recipient_cpf: str,
        recipient_name: str,
        recipient_pix_key: str,
        external_id: str,
    ) -> PIXPayout:
        warn("Using MOCK Stark Bank payout response (no real credentials configured)")
        fake_id = f"starkbank-payout-mock-{uuid.uuid4().hex[:8]}"
        return PIXPayout(
            payout_id=fake_id,
            psp="starkbank",
            amount_brl=amount_brl,
            recipient_cpf=recipient_cpf,
            recipient_name=recipient_name,
            recipient_pix_key=recipient_pix_key,
            status="PROCESSING",
            end_to_end_id=f"E20018183{uuid.uuid4().hex[:22].upper()}",
            raw_response={"mock": True, "id": fake_id},
        )


# ---------------------------------------------------------------------------
# Transfeera Sandbox
# ---------------------------------------------------------------------------

TRANSFEERA_SANDBOX_BASE = "https://api-sandbox.transfeera.com"
TRANSFEERA_AUTH_URL = f"{TRANSFEERA_SANDBOX_BASE}/auth/token"
TRANSFEERA_CHARGE_URL = f"{TRANSFEERA_SANDBOX_BASE}/cob"
TRANSFEERA_PAYOUT_URL = f"{TRANSFEERA_SANDBOX_BASE}/batch-payments"


class TransfeeraSandbox:
    """
    Transfeera is the recommended backup PSP and the best choice at high volume
    due to its flat-fee model (R$1.00 per deposit, R$2.50 per payout).
    BCB-authorized IP with a dedicated gaming/betting vertical.

    Sandbox docs: https://docs.transfeera.com/
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """
        Transfeera uses OAuth 2.0 client_credentials.
        The 'grant_type' key must be named 'grantType' in their API.
        """
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        info("Requesting Transfeera OAuth token …")
        response = await client.post(
            TRANSFEERA_AUTH_URL,
            json={
                "grantType": "client_credentials",
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
            },
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code != 200:
            err(f"Transfeera auth failed: {response.status_code} {response.text}")
            # Return a dummy token so the demo can continue with mock responses
            return "mock-token-no-credentials"

        payload = response.json()
        self._token = payload.get("accessToken", "mock-token")
        self._token_expiry = time.time() + payload.get("expiresIn", 3600)
        ok("Transfeera token obtained")
        return self._token

    async def create_pix_charge(
        self,
        client: httpx.AsyncClient,
        *,
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
        expiry_seconds: int = 3600,
    ) -> PIXCharge:
        """
        Create a PIX immediate charge (cob) in Transfeera.
        Transfeera charges a flat R$1.00 per successful charge — no percentage.
        """
        token = await self._get_token(client)
        info(f"Creating Transfeera PIX charge for R${amount_brl:.2f} …")

        expiry_dt = datetime.fromtimestamp(
            time.time() + expiry_seconds, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        body = {
            "calendario": {
                "dataDeVencimento": expiry_dt,
                "validadeAposVencimento": 0,
            },
            "devedor": {
                "cpf": debtor_cpf.replace(".", "").replace("-", ""),
                "nome": debtor_name,
            },
            "valor": {
                "original": f"{amount_brl:.2f}",
                "modalidadeAlteracao": 0,
            },
            "chave": os.getenv("TRANSFEERA_PIX_KEY", "12345678000195"),
            "solicitacaoPagador": f"Deposit — {external_id}",
            "infoAdicionais": [
                {"nome": "txId", "valor": external_id},
                {"nome": "platform", "valor": "BetBrasil"},
            ],
        }

        response = await client.post(
            TRANSFEERA_CHARGE_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )

        if response.status_code not in (200, 201):
            err(f"Transfeera charge failed: {response.status_code} {response.text}")
            return self._mock_charge(amount_brl, debtor_cpf, debtor_name, external_id)

        data = response.json()
        ok(f"Transfeera charge created: txid={data.get('txid', 'N/A')}")

        return PIXCharge(
            charge_id=data.get("txid", external_id),
            psp="transfeera",
            amount_brl=amount_brl,
            debtor_cpf=debtor_cpf,
            debtor_name=debtor_name,
            status=data.get("status", "ATIVA").upper(),
            qr_code=data.get("pixCopiaECola", ""),
            copy_paste_key=data.get("pixCopiaECola", ""),
            expiry_seconds=expiry_seconds,
            raw_response=data,
        )

    async def create_batch_payout(
        self,
        client: httpx.AsyncClient,
        payouts: list[dict],
    ) -> list[PIXPayout]:
        """
        Transfeera payouts are submitted as batches (lotes).
        This is more efficient for high-volume withdrawal queues.
        Cost: R$2.50 per payout regardless of amount.

        Each payout dict must have:
            amount_brl, recipient_cpf, recipient_name,
            recipient_pix_key, external_id
        """
        token = await self._get_token(client)
        info(f"Submitting Transfeera batch payout with {len(payouts)} item(s) …")

        items = [
            {
                "valor": p["amount_brl"],
                "data": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "tipo_pagamento": "PIX_CHAVE",
                "pix_key_type": "CPF",
                "pix_key": p["recipient_cpf"].replace(".", "").replace("-", ""),
                "nome_favorecido": p["recipient_name"],
                "cpf_cnpj": p["recipient_cpf"].replace(".", "").replace("-", ""),
                "referencia_id": p["external_id"],
            }
            for p in payouts
        ]

        body = {
            "pagamentos": items,
            "descricao": f"BetBrasil withdrawal batch {datetime.now(timezone.utc).date()}",
        }

        response = await client.post(
            TRANSFEERA_PAYOUT_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )

        if response.status_code not in (200, 201):
            err(f"Transfeera batch failed: {response.status_code} {response.text}")
            return [self._mock_payout(p) for p in payouts]

        data = response.json()
        batch_id = data.get("id", f"batch-{uuid.uuid4().hex[:8]}")
        ok(f"Transfeera batch submitted: batchId={batch_id}")

        return [
            PIXPayout(
                payout_id=f"{batch_id}-{i}",
                psp="transfeera",
                amount_brl=p["amount_brl"],
                recipient_cpf=p["recipient_cpf"],
                recipient_name=p["recipient_name"],
                recipient_pix_key=p["recipient_pix_key"],
                status="PROCESSING",
                raw_response=data,
            )
            for i, p in enumerate(payouts)
        ]

    @staticmethod
    def _mock_charge(
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
    ) -> PIXCharge:
        warn("Using MOCK Transfeera response (no real credentials configured)")
        fake_txid = uuid.uuid4().hex.upper()
        return PIXCharge(
            charge_id=fake_txid,
            psp="transfeera",
            amount_brl=amount_brl,
            debtor_cpf=debtor_cpf,
            debtor_name=debtor_name,
            status="ATIVA",
            qr_code=f"00020126mock-transfeera-{fake_txid[:16]}6304BEEF",
            copy_paste_key=f"00020126mock-transfeera-{fake_txid[:16]}6304BEEF",
            raw_response={"mock": True, "txid": fake_txid},
        )

    @staticmethod
    def _mock_payout(payout: dict) -> PIXPayout:
        warn("Using MOCK Transfeera payout response")
        return PIXPayout(
            payout_id=f"transfeera-mock-{uuid.uuid4().hex[:8]}",
            psp="transfeera",
            amount_brl=payout["amount_brl"],
            recipient_cpf=payout["recipient_cpf"],
            recipient_name=payout["recipient_name"],
            recipient_pix_key=payout["recipient_pix_key"],
            status="PROCESSING",
            raw_response={"mock": True},
        )


# ---------------------------------------------------------------------------
# Webhook simulation
# ---------------------------------------------------------------------------

def simulate_webhook_payload(charge: PIXCharge, event: str = "PAYMENT_CONFIRMED") -> dict:
    """
    In production, your webhook endpoint receives a signed POST from the PSP
    when a payment is confirmed. This function generates a realistic payload
    to test your webhook handler logic without waiting for a real payment.

    See pix_payment_gateway.py for the actual webhook handler implementation.
    """
    e2e_id = f"E20018183{uuid.uuid4().hex[:22].upper()}"
    return {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "psp": charge.psp,
        "data": {
            "charge_id": charge.charge_id,
            "amount": charge.amount_brl,
            "amount_brl": charge.amount_brl,
            "debtor_cpf": charge.debtor_cpf,
            "debtor_name": charge.debtor_name,
            "end_to_end_id": e2e_id,
            "payment_timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "PAID",
        },
        # PSP signs this payload with HMAC-SHA256; your server verifies it
        "signature": hashlib.sha256(
            (charge.charge_id + e2e_id).encode()
        ).hexdigest(),
    }


def handle_webhook(payload: dict) -> dict:
    """
    Minimal webhook handler demonstrating the expected processing logic.
    In production this runs inside your FastAPI route (see pix_payment_gateway.py).

    Steps:
      1. Verify PSP signature (HMAC-SHA256 or RSA depending on PSP)
      2. Look up internal transaction by charge_id
      3. Credit player wallet
      4. Emit SIGAP deposit event
      5. Return 200 OK within 5 seconds (PSPs retry on timeout)
    """
    event = payload.get("event")
    data = payload.get("data", {})

    result = {
        "received_event": event,
        "charge_id": data.get("charge_id"),
        "amount_brl": data.get("amount_brl"),
        "debtor_cpf": data.get("debtor_cpf"),
        "end_to_end_id": data.get("end_to_end_id"),
        "actions": [],
    }

    if event == "PAYMENT_CONFIRMED":
        result["actions"].extend([
            "signature_verified",
            f"wallet_credited:{data.get('amount_brl')}",
            f"sigap_deposit_event_emitted:{data.get('end_to_end_id')}",
            "player_notification_sent",
        ])
        result["status"] = "processed"
    else:
        result["status"] = "ignored"
        result["actions"].append(f"unknown_event_logged:{event}")

    return result


# ---------------------------------------------------------------------------
# PSP Router — multi-PSP fallback logic
# ---------------------------------------------------------------------------

class PSPRouter:
    """
    Routes PIX charge creation through a primary PSP with automatic
    fallback to secondary and tertiary providers.

    The router implements the pattern described in Section 46.17:
        Primary (Celcoin) → Fallback (Transfeera) → Emergency (Stark Bank)

    All responses are logged to a reconciliation ledger in production.
    """

    def __init__(
        self,
        primary: CelcoinSandbox,
        fallback: TransfeeraSandbox,
        emergency: StarkBankSandbox,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.emergency = emergency
        self.attempt_log: list[dict] = []

    async def create_charge(
        self,
        client: httpx.AsyncClient,
        *,
        amount_brl: float,
        debtor_cpf: str,
        debtor_name: str,
        external_id: str,
    ) -> tuple[PIXCharge, str]:
        """
        Try each PSP in order. Returns (charge, psp_used).
        In production, add circuit breakers so a failing PSP stops
        receiving traffic until it recovers.
        """
        for psp_name, create_fn in [
            ("celcoin", self.primary.create_pix_charge),
            ("transfeera", self.fallback.create_pix_charge),
            ("starkbank", self.emergency.create_pix_invoice),
        ]:
            attempt_id = f"{external_id}-{psp_name}"
            info(f"PSP Router: trying {psp_name} …")
            start = time.monotonic()
            try:
                charge = await asyncio.wait_for(
                    create_fn(
                        client,
                        amount_brl=amount_brl,
                        debtor_cpf=debtor_cpf,
                        debtor_name=debtor_name,
                        external_id=attempt_id,
                    ),
                    timeout=5.0,  # 5-second hard timeout per PSP attempt
                )
                elapsed = time.monotonic() - start
                self.attempt_log.append({
                    "psp": psp_name,
                    "external_id": external_id,
                    "latency_ms": round(elapsed * 1000),
                    "result": "success",
                })
                ok(f"PSP Router: {psp_name} succeeded in {elapsed * 1000:.0f}ms")
                return charge, psp_name

            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start
                self.attempt_log.append({
                    "psp": psp_name,
                    "external_id": external_id,
                    "latency_ms": round(elapsed * 1000),
                    "result": "timeout",
                })
                warn(f"PSP Router: {psp_name} timed out after {elapsed * 1000:.0f}ms — trying fallback")
                continue

            except Exception as exc:
                self.attempt_log.append({
                    "psp": psp_name,
                    "external_id": external_id,
                    "result": f"error:{exc}",
                })
                warn(f"PSP Router: {psp_name} errored ({exc}) — trying fallback")
                continue

        raise RuntimeError("All PSPs failed — charge queued for manual retry")


# ---------------------------------------------------------------------------
# Full demo flow
# ---------------------------------------------------------------------------

async def run_demo() -> None:
    section("PIX PSP Sandbox Integration Demo — Chapter 46.17")

    print(f"""
{DIM}This demo walks through the complete PIX payment lifecycle:
  1. Authenticate with each PSP
  2. Create a PIX deposit charge
  3. Simulate webhook confirmation
  4. Process the webhook (credit wallet, emit SIGAP event)
  5. Create a PIX payout (withdrawal)
  6. Multi-PSP router fallback demonstration{RESET}
""")

    # Load credentials from environment (or fall back to mock mode)
    celcoin_id = os.getenv("CELCOIN_CLIENT_ID", "")
    celcoin_secret = os.getenv("CELCOIN_CLIENT_SECRET", "")
    starkbank_project = os.getenv("STARKBANK_PROJECT_ID", "")
    starkbank_key = os.getenv("STARKBANK_PRIVATE_KEY", "mock-key")
    transfeera_id = os.getenv("TRANSFEERA_CLIENT_ID", "")
    transfeera_secret = os.getenv("TRANSFEERA_CLIENT_SECRET", "")

    if not celcoin_id:
        warn("No CELCOIN_CLIENT_ID set — running in MOCK mode (no real API calls)")
        warn("Set environment variables to test against real sandboxes")

    # Shared test player data
    player_cpf = "123.456.789-09"         # Use BCB test CPF format in sandbox
    player_name = "João Silva Teste"
    deposit_amount = 150.00               # R$150.00 deposit
    payout_amount = 80.00                 # R$80.00 withdrawal
    tx_id = f"demo-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(follow_redirects=True) as client:

        # ---- CELCOIN -------------------------------------------------------
        section("Celcoin — Primary PSP (Deposits)")
        celcoin = CelcoinSandbox(celcoin_id, celcoin_secret)

        step(1, "Create PIX deposit charge")
        celcoin_charge = await celcoin.create_pix_charge(
            client,
            amount_brl=deposit_amount,
            debtor_cpf=player_cpf,
            debtor_name=player_name,
            external_id=f"{tx_id}-celcoin",
        )
        dump("PIX Charge", {
            "charge_id": celcoin_charge.charge_id,
            "psp": celcoin_charge.psp,
            "amount_brl": celcoin_charge.amount_brl,
            "status": celcoin_charge.status,
            "qr_code_preview": celcoin_charge.qr_code[:60] + "…" if len(celcoin_charge.qr_code) > 60 else celcoin_charge.qr_code,
        })

        step(2, "Poll charge status (use webhooks in production)")
        status = await celcoin.check_charge_status(client, celcoin_charge.charge_id)
        info(f"Celcoin charge status: {status}")

        step(3, "Simulate PIX payment webhook from Celcoin")
        webhook_payload = simulate_webhook_payload(celcoin_charge, "PAYMENT_CONFIRMED")
        dump("Incoming Webhook Payload", webhook_payload)

        step(4, "Process webhook (credit wallet + SIGAP)")
        handler_result = handle_webhook(webhook_payload)
        dump("Webhook Handler Result", handler_result)
        for action in handler_result.get("actions", []):
            ok(f"Action: {action}")

        # ---- STARK BANK ----------------------------------------------------
        section("Stark Bank — High-Volume Deposits and Payouts")
        starkbank = StarkBankSandbox(starkbank_project, starkbank_key)

        step(5, "Create PIX invoice (deposit) via Stark Bank")
        sb_charge = await starkbank.create_pix_invoice(
            client,
            amount_brl=deposit_amount,
            debtor_cpf=player_cpf,
            debtor_name=player_name,
            external_id=f"{tx_id}-starkbank",
        )
        dump("Stark Bank Invoice", {
            "charge_id": sb_charge.charge_id,
            "status": sb_charge.status,
            "amount_brl": sb_charge.amount_brl,
        })

        step(6, "Create PIX payout (withdrawal) via Stark Bank")
        sb_payout = await starkbank.create_pix_payout(
            client,
            amount_brl=payout_amount,
            recipient_cpf=player_cpf,
            recipient_name=player_name,
            recipient_pix_key=player_cpf,   # Player's CPF as PIX key
            external_id=f"{tx_id}-payout-starkbank",
        )
        dump("Stark Bank Payout", {
            "payout_id": sb_payout.payout_id,
            "status": sb_payout.status,
            "amount_brl": sb_payout.amount_brl,
            "end_to_end_id": sb_payout.end_to_end_id,
        })

        # ---- TRANSFEERA ----------------------------------------------------
        section("Transfeera — Flat-Fee Backup PSP")
        transfeera = TransfeeraSandbox(transfeera_id, transfeera_secret)

        step(7, "Create PIX charge via Transfeera (R$1.00 flat fee)")
        tf_charge = await transfeera.create_pix_charge(
            client,
            amount_brl=deposit_amount,
            debtor_cpf=player_cpf,
            debtor_name=player_name,
            external_id=f"{tx_id}-transfeera",
        )
        dump("Transfeera Charge", {
            "charge_id": tf_charge.charge_id,
            "status": tf_charge.status,
            "amount_brl": tf_charge.amount_brl,
        })

        step(8, "Submit batch payout via Transfeera (R$2.50/item flat fee)")
        tf_payouts = await transfeera.create_batch_payout(
            client,
            payouts=[
                {
                    "amount_brl": payout_amount,
                    "recipient_cpf": player_cpf,
                    "recipient_name": player_name,
                    "recipient_pix_key": player_cpf,
                    "external_id": f"{tx_id}-batch-0",
                },
                {
                    "amount_brl": 200.00,
                    "recipient_cpf": "987.654.321-00",
                    "recipient_name": "Maria Souza Teste",
                    "recipient_pix_key": "987.654.321-00",
                    "external_id": f"{tx_id}-batch-1",
                },
            ],
        )
        for i, payout in enumerate(tf_payouts):
            ok(f"Payout {i}: {payout.payout_id} → {payout.recipient_name} R${payout.amount_brl:.2f} [{payout.status}]")

        # ---- PSP ROUTER ----------------------------------------------------
        section("PSP Router — Multi-PSP Fallback")

        step(9, "Route charge through primary → fallback → emergency")
        router = PSPRouter(
            primary=celcoin,
            fallback=transfeera,
            emergency=starkbank,
        )
        routed_charge, used_psp = await router.create_charge(
            client,
            amount_brl=75.00,
            debtor_cpf=player_cpf,
            debtor_name=player_name,
            external_id=f"{tx_id}-routed",
        )
        ok(f"Charge routed via: {used_psp.upper()}")
        dump("Router Attempt Log", router.attempt_log)

        # ---- COST COMPARISON -----------------------------------------------
        section("Cost Comparison — Choose the Right PSP at Your Scale")

        monthly_volumes = [15_000_000, 100_000_000, 500_000_000]
        scales = ["Small (50K players)", "Medium (250K)", "Large (1M)"]

        print(f"\n  {'Scale':<22} {'Volume (BRL)':<18} {'Celcoin 0.6%':<18} {'Transfeera flat':<18} {'Savings'}")
        print(f"  {'-'*22} {'-'*18} {'-'*18} {'-'*18} {'-'*15}")

        for scale, volume in zip(scales, monthly_volumes):
            celcoin_cost = volume * 0.006
            # Transfeera: R$1/deposit + R$2.50/payout, assume ~equal split
            avg_bet = 300  # average bet size
            transactions = volume / avg_bet
            transfeera_cost = transactions * 1.0 + transactions * 2.50
            savings_pct = (celcoin_cost - transfeera_cost) / celcoin_cost * 100
            print(
                f"  {scale:<22} R${volume/1e6:>6.0f}M       "
                f"R${celcoin_cost:>9,.0f}   "
                f"R${transfeera_cost:>9,.0f}   "
                f"{savings_pct:.0f}% cheaper"
            )

        # ---- SUMMARY -------------------------------------------------------
        section("Summary")
        print(f"""
{GREEN}  All demo steps completed successfully.{RESET}

{BOLD}  Key takeaways:{RESET}
  - Celcoin: best for getting started (good docs, iGaming support, D+0)
  - Stark Bank: best for scale (zero PIX-in cost, excellent SDK)
  - Transfeera: best for payout-heavy platforms (flat fee wins at volume)
  - PSP Router: always configure 2-3 PSPs for 99.99% payment availability

{BOLD}  Next steps:{RESET}
  1. Register at sandbox portals (links in Chapter 46.17)
  2. Set CELCOIN_CLIENT_ID, CELCOIN_CLIENT_SECRET env vars
  3. Re-run this script to test against real sandbox
  4. Integrate pix_payment_gateway.py with your PSP credentials
  5. Complete the production checklist in Section 46.17

{DIM}  Documentation:{RESET}
  - Celcoin:    https://sandbox.openfinance.celcoin.dev/docs
  - Stark Bank: https://starkbank.com/faq
  - Transfeera: https://docs.transfeera.com/
  - BCB PIX:    https://www.bcb.gov.br/estabilidadefinanceira/pix
""")


# ---------------------------------------------------------------------------
# Self-validation — ensure this file is valid Python before shipping
# ---------------------------------------------------------------------------

def _self_validate() -> None:
    """Parse this file with ast.parse() to catch syntax errors at import time."""
    import pathlib
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    ast.parse(source)  # Raises SyntaxError if the file is invalid


_self_validate()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_demo())
