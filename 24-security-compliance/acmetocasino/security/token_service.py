# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
token_service — HMAC-Signed Game Launch Tokens
================================================

``TokenService`` creates short-lived, tamper-evident game launch tokens that
carry all the context a supplier RGS (Remote Game Server) needs to authenticate
a wallet callback:

* **Player identity** — ``player_id``, ``brand_id``
* **Game context** — ``game_id``, ``supplier_id``, ``is_mobile``
* **Anti-replay protection** — a cryptographically random nonce so that each
  token can only be used once (the caller is responsible for tracking used
  nonces if replay prevention is required).
* **Expiry** — a short TTL (default 5 minutes) limits the window for abuse.

Signing
-------
Tokens are encoded as ``base64url(header.payload).base64url(signature)``
where the signature is ``HMAC-SHA256(secret_key, header.payload)``.

This is intentionally **not** a JWT — the structure is simpler, the payload is
JSON-encoded, and there is no algorithm field in the header (which avoids the
``"alg": "none"`` class of JWT vulnerabilities).

Thread safety
~~~~~~~~~~~~~
``TokenService`` is stateless; all state is embedded in the token itself.
Instances may be shared across threads freely.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Payload model
# ---------------------------------------------------------------------------


class GameTokenPayload(BaseModel):
    """The decoded, validated payload of a game launch token.

    Attributes
    ----------
    player_id:
        Platform player identifier.
    game_id:
        The game being launched (supplier-specific identifier).
    brand_id:
        The white-label brand context.
    supplier_id:
        The supplier / RGS that will serve this game.
    is_mobile:
        Whether the session was initiated from a mobile device.
    issued_at:
        UTC timestamp when the token was created.
    expires_at:
        UTC timestamp after which the token must be rejected.
    nonce:
        Cryptographically random 128-bit hex string.  Used to detect
        replayed tokens when the caller maintains a nonce cache.
    """

    model_config = ConfigDict(frozen=True)

    player_id: str = Field(..., description="Platform player UUID")
    game_id: str = Field(..., description="Supplier game identifier")
    brand_id: str = Field(..., description="White-label brand identifier")
    supplier_id: str = Field(..., description="RGS supplier identifier")
    is_mobile: bool = Field(False, description="Mobile session flag")
    issued_at: datetime = Field(..., description="UTC token issuance timestamp")
    expires_at: datetime = Field(..., description="UTC token expiry timestamp")
    nonce: str = Field(..., description="128-bit anti-replay nonce (hex)")

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return ``True`` if this payload has passed its expiry time."""
        reference = now or datetime.now(tz=timezone.utc)
        expires = self.expires_at
        # Ensure both are offset-aware for comparison
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return reference >= expires


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """Base class for token-related errors."""


class TokenExpiredError(TokenError):
    """Raised when a decoded token has passed its expiry time."""


class TokenInvalidError(TokenError):
    """Raised when a token cannot be decoded or its signature is invalid."""


# ---------------------------------------------------------------------------
# TokenService
# ---------------------------------------------------------------------------


class TokenService:
    """Creates and decodes HMAC-SHA256–signed game launch tokens.

    Parameters
    ----------
    secret_key:
        Shared secret used for HMAC signing and verification.  Must be kept
        confidential.  In production, load from a secrets manager (e.g. AWS
        Secrets Manager, HashiCorp Vault).
    token_ttl_seconds:
        How long (in seconds) a created token is valid.  Default: 300 (5 min).

    Examples
    --------
    >>> svc = TokenService(secret_key="super-secret")
    >>> token = svc.create_game_token("player-1", "game-abc", "brand-x", "pragmatic", False)
    >>> payload = svc.decode_game_token(token)
    >>> payload.player_id
    'player-1'
    """

    # Token structure: base64url(payload_json) + "." + base64url(hmac_sig)
    _SEPARATOR = "."

    def __init__(
        self,
        *,
        secret_key: str,
        token_ttl_seconds: int = 300,
    ) -> None:
        if not secret_key:
            raise ValueError("secret_key must not be empty")
        self._secret = secret_key.encode()
        self._ttl = timedelta(seconds=token_ttl_seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_game_token(
        self,
        player_id: str,
        game_id: str,
        brand_id: str,
        supplier_id: str,
        is_mobile: bool,
    ) -> str:
        """Create a signed game launch token.

        Parameters
        ----------
        player_id:
            Platform player identifier.
        game_id:
            Supplier-specific game identifier.
        brand_id:
            White-label brand identifier.
        supplier_id:
            RGS supplier identifier.
        is_mobile:
            Whether the client is a mobile browser or app.

        Returns
        -------
        str
            Opaque token string: ``base64url(payload) + "." + base64url(sig)``.
        """
        now = datetime.now(tz=timezone.utc)
        payload = GameTokenPayload(
            player_id=player_id,
            game_id=game_id,
            brand_id=brand_id,
            supplier_id=supplier_id,
            is_mobile=is_mobile,
            issued_at=now,
            expires_at=now + self._ttl,
            nonce=secrets.token_hex(16),  # 128-bit nonce
        )
        return self._encode(payload)

    def decode_game_token(self, token: str) -> GameTokenPayload:
        """Decode and validate *token*, returning the embedded payload.

        Parameters
        ----------
        token:
            Token string previously returned by :meth:`create_game_token`.

        Returns
        -------
        GameTokenPayload
            The validated, decoded payload.

        Raises
        ------
        TokenInvalidError
            If the token is malformed, tampered with, or cannot be parsed.
        TokenExpiredError
            If the token's TTL has elapsed.
        """
        parts = token.split(self._SEPARATOR, maxsplit=1)
        if len(parts) != 2:
            raise TokenInvalidError("Token format is invalid — expected two segments")

        encoded_payload, encoded_sig = parts

        # Verify signature before decoding payload (timing-safe compare)
        expected_sig = self._sign(encoded_payload)
        try:
            provided_sig = base64.urlsafe_b64decode(
                self._pad(encoded_sig)
            )
        except Exception as exc:
            raise TokenInvalidError("Token signature segment is not valid base64") from exc

        if not hmac.compare_digest(expected_sig, provided_sig):
            raise TokenInvalidError("Token signature verification failed")

        # Decode payload
        try:
            payload_json = base64.urlsafe_b64decode(
                self._pad(encoded_payload)
            ).decode()
            data = json.loads(payload_json)
            payload = GameTokenPayload.model_validate(data)
        except Exception as exc:
            raise TokenInvalidError(f"Token payload cannot be decoded: {exc}") from exc

        if payload.is_expired():
            raise TokenExpiredError(
                f"Game token expired at {payload.expires_at.isoformat()}"
            )

        return payload

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _encode(self, payload: GameTokenPayload) -> str:
        """Serialise *payload* to a signed token string."""
        payload_json = payload.model_dump_json()
        encoded_payload = base64.urlsafe_b64encode(
            payload_json.encode()
        ).rstrip(b"=").decode()

        sig = self._sign(encoded_payload)
        encoded_sig = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

        return f"{encoded_payload}{self._SEPARATOR}{encoded_sig}"

    def _sign(self, message: str) -> bytes:
        """Return the HMAC-SHA256 digest of *message* using the service secret."""
        return hmac.new(
            self._secret,
            message.encode(),
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _pad(b64: str) -> str:
        """Restore stripped base64url padding."""
        padding_needed = (4 - len(b64) % 4) % 4
        return b64 + "=" * padding_needed
