# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.models.supplier_capabilities — SupplierCapabilities
================================================================

Describes what a supplier integration can and cannot do.  The platform uses
this at game-launch time to validate that the requested session mode,
channel, and features are actually supported before any supplier call is made.

This avoids embarrassing runtime failures caused by, e.g., asking a
slots-only supplier to host a live-casino session, or sending a free-round
request to a supplier that has never implemented that feature.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from acmetocasino.gameservice.models.enums import CallbackStyle, ProductType


class SupplierCapabilities(BaseModel):
    """Static declaration of a supplier integration's capabilities.

    An instance of this class is returned by every ``SupplierAdapter`` and
    stored in the :class:`~acmetocasino.gameservice.suppliers.CapabilityMatrix`
    so the platform can answer capability queries without constructing adapters.

    Attributes
    ----------
    supplier_id:
        Unique platform identifier (e.g. ``"pragmatic"``, ``"netent"``).
    name:
        Human-readable display name (e.g. ``"Pragmatic Play"``).
    product_types:
        The broad game categories this supplier offers.
    supports_free_rounds:
        Whether the supplier has implemented the free-round callback protocol.
        Required for running free-round bonuses on their content.
    supports_jackpots:
        Whether the supplier participates in a jackpot contribution / payout
        scheme.
    supports_tipping:
        Whether the supplier supports live-casino dealer tipping (a separate
        TIP command type).
    supports_tournaments:
        Whether the supplier exposes a tournament leaderboard API that the
        platform can consume.
    callback_style:
        The integration model: PULL, PUSH, or SEAMLESS.
    supported_currencies:
        ISO-4217 codes accepted by this supplier.  An empty list means the
        supplier advertises support for all currencies, but in practice this
        is rare — list them explicitly.
    supported_jurisdictions:
        Jurisdiction codes for which the supplier holds a licence (or has
        passed compliance review).  A game from this supplier will only be
        launchable in these jurisdictions.
    min_bet:
        Minimum bet amount (in the supplier's base denomination).  ``None``
        means the supplier does not impose a platform-level minimum.
    max_bet:
        Maximum bet amount.  ``None`` means no cap is declared.
    rtp_configurable:
        Whether the operator can configure per-game RTP levels via the
        supplier's backoffice API.
    """

    model_config = {"frozen": True}

    supplier_id: str = Field(..., min_length=1, description="Unique platform supplier ID.")
    name: str = Field(..., min_length=1, description="Human-readable supplier name.")
    product_types: list[ProductType] = Field(
        ...,
        min_length=1,
        description="Game categories offered by this supplier.",
    )
    supports_free_rounds: bool = Field(
        default=False,
        description="Supplier implements the free-round callback protocol.",
    )
    supports_jackpots: bool = Field(
        default=False,
        description="Supplier participates in jackpot contribution/payout.",
    )
    supports_tipping: bool = Field(
        default=False,
        description="Supplier supports live-dealer tipping (TIP command).",
    )
    supports_tournaments: bool = Field(
        default=False,
        description="Supplier exposes a tournament leaderboard API.",
    )
    callback_style: CallbackStyle = Field(
        ...,
        description="Wallet integration model used by this supplier.",
    )
    supported_currencies: list[str] = Field(
        default_factory=list,
        description="ISO-4217 currency codes accepted by this supplier.",
    )
    supported_jurisdictions: list[str] = Field(
        default_factory=list,
        description="Jurisdiction codes for which this supplier is approved.",
    )
    min_bet: str | None = Field(
        default=None,
        description="Supplier-declared minimum bet in base denomination.",
    )
    max_bet: str | None = Field(
        default=None,
        description="Supplier-declared maximum bet (None = no cap).",
    )
    rtp_configurable: bool = Field(
        default=False,
        description="Whether the operator can configure per-game RTP levels.",
    )

    def supports_currency(self, currency: str) -> bool:
        """Return ``True`` if ``currency`` is in the supplier's supported list.

        An empty ``supported_currencies`` list is interpreted as *all
        currencies supported* — consistent with a supplier that hasn't yet
        provided an explicit allowlist.
        """
        if not self.supported_currencies:
            return True
        return currency.upper() in {c.upper() for c in self.supported_currencies}

    def supports_jurisdiction(self, jurisdiction: str) -> bool:
        """Return ``True`` if ``jurisdiction`` is in the supported list.

        Same empty-list semantics as :meth:`supports_currency`.
        """
        if not self.supported_jurisdictions:
            return True
        return jurisdiction.upper() in {j.upper() for j in self.supported_jurisdictions}

    def has_product(self, product_type: ProductType) -> bool:
        """Return ``True`` if the supplier offers the given product category."""
        return product_type in self.product_types

    def __repr__(self) -> str:
        products = ", ".join(p.value for p in self.product_types)
        return (
            f"SupplierCapabilities(id={self.supplier_id!r}, "
            f"style={self.callback_style.value!r}, "
            f"products=[{products}])"
        )


__all__ = ["SupplierCapabilities"]
