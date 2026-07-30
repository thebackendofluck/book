# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Jurisdiction-aware currency assignment.

Python port of CurrencyService from the Scala production service
referenced in chapter 26. The service resolves the correct playing
currency for a (brand, country) pair, respecting three layers of
configuration in this order:

1. **Per-country availability constraints** -- each country has an
   explicit allow-list of currencies. A brand that tries to offer
   a currency not in the country's allow-list will be rejected at
   this layer, even if the brand supports it elsewhere.
2. **Brand currencies** -- each brand has an ordered preference list
   of currencies. The first brand currency that is also in the
   country's allow-list wins.
3. **Country default currency** -- falls through to the country's
   default when the brand preference and country allow-list do not
   intersect. If the country has no default (unknown or unconfigured
   country), the service returns `None`.

The canonical call site is the registration flow: after the player
selects a country, the platform calls `resolve_for_registration(brand,
country)` to determine which currency to pre-select in the wallet.

The reference data is embedded in-module for self-containment. A
production deployment swaps these for rows loaded from the operator
database, with caching driven by the same CRC-style update-detection
pattern as GeoIpDatabase.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Currency:
    """ISO 4217 currency record."""

    iso_code: str       # ISO 4217 alpha-3, e.g. "EUR", "BRL"
    display_symbol: str  # "€", "R$", etc.
    decimal_places: int  # 0 for JPY, 2 for most fiat, 8 for BTC-style


@dataclass(frozen=True)
class CountryCurrencyProfile:
    """Per-country currency configuration.

    `default` is what the player gets if no brand preference matches.
    `allowed` is the hard allow-list: anything not in here is rejected
    even if a brand tries to offer it. A country with an empty
    `allowed` set is treated as "no currency configured" and
    `resolve_for_registration` returns None.
    """

    country_iso: str
    default: str | None
    allowed: frozenset[str]


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

_CURRENCIES: tuple[Currency, ...] = (
    Currency("USD", "$", 2),
    Currency("EUR", "€", 2),
    Currency("GBP", "£", 2),
    Currency("BRL", "R$", 2),
    Currency("CAD", "C$", 2),
    Currency("AUD", "A$", 2),
    Currency("JPY", "¥", 0),
    Currency("CHF", "CHF", 2),
    Currency("SEK", "kr", 2),
    Currency("NOK", "kr", 2),
    Currency("DKK", "kr", 2),
    Currency("MXN", "Mex$", 2),
    Currency("ARS", "AR$", 2),
    Currency("CLP", "CLP$", 0),
    Currency("COP", "COL$", 2),
    Currency("PEN", "S/", 2),
)

_CURRENCIES_BY_ISO: dict[str, Currency] = {c.iso_code: c for c in _CURRENCIES}

_COUNTRY_PROFILES: dict[str, CountryCurrencyProfile] = {
    "US": CountryCurrencyProfile("US", "USD", frozenset({"USD"})),
    "CA": CountryCurrencyProfile("CA", "CAD", frozenset({"CAD", "USD"})),
    "GB": CountryCurrencyProfile("GB", "GBP", frozenset({"GBP", "EUR"})),
    "BR": CountryCurrencyProfile("BR", "BRL", frozenset({"BRL"})),
    "MX": CountryCurrencyProfile("MX", "MXN", frozenset({"MXN", "USD"})),
    "AR": CountryCurrencyProfile("AR", "ARS", frozenset({"ARS", "USD"})),
    "CL": CountryCurrencyProfile("CL", "CLP", frozenset({"CLP", "USD"})),
    "CO": CountryCurrencyProfile("CO", "COP", frozenset({"COP", "USD"})),
    "PE": CountryCurrencyProfile("PE", "PEN", frozenset({"PEN", "USD"})),
    "JP": CountryCurrencyProfile("JP", "JPY", frozenset({"JPY"})),
    "AU": CountryCurrencyProfile("AU", "AUD", frozenset({"AUD"})),
    "NZ": CountryCurrencyProfile("NZ", "AUD", frozenset({"AUD"})),
    "CH": CountryCurrencyProfile("CH", "CHF", frozenset({"CHF", "EUR"})),
    "SE": CountryCurrencyProfile("SE", "SEK", frozenset({"SEK", "EUR"})),
    "NO": CountryCurrencyProfile("NO", "NOK", frozenset({"NOK", "EUR"})),
    "DK": CountryCurrencyProfile("DK", "DKK", frozenset({"DKK", "EUR"})),
    # EU members default to EUR and accept only EUR by default; a local
    # currency override is added only where the operator has a licence
    # to offer it.
    "DE": CountryCurrencyProfile("DE", "EUR", frozenset({"EUR"})),
    "FR": CountryCurrencyProfile("FR", "EUR", frozenset({"EUR"})),
    "IT": CountryCurrencyProfile("IT", "EUR", frozenset({"EUR"})),
    "ES": CountryCurrencyProfile("ES", "EUR", frozenset({"EUR"})),
    "PT": CountryCurrencyProfile("PT", "EUR", frozenset({"EUR"})),
    "NL": CountryCurrencyProfile("NL", "EUR", frozenset({"EUR"})),
    "BE": CountryCurrencyProfile("BE", "EUR", frozenset({"EUR"})),
    "AT": CountryCurrencyProfile("AT", "EUR", frozenset({"EUR"})),
    "IE": CountryCurrencyProfile("IE", "EUR", frozenset({"EUR"})),
    "MT": CountryCurrencyProfile("MT", "EUR", frozenset({"EUR"})),
    "FI": CountryCurrencyProfile("FI", "EUR", frozenset({"EUR"})),
}


@dataclass
class CurrencyService:
    """Resolve per-brand, per-country playing currency."""

    # Brand preferences: each brand has an ordered list of preferred
    # currencies. The service iterates this list and returns the first
    # entry that is also in the country's allow-list.
    brand_preferences: dict[str, list[str]] = field(default_factory=dict)

    def register_brand(self, brand: str, preferences: list[str]) -> None:
        """Record a brand's ordered currency preference list.

        The first currency in the list is the brand's globally preferred
        currency; subsequent entries are fallbacks used when a country's
        allow-list does not include the top choice.
        """
        if not preferences:
            raise ValueError(f"brand {brand} needs at least one preferred currency")
        for iso in preferences:
            if iso not in _CURRENCIES_BY_ISO:
                raise ValueError(f"unknown currency {iso} for brand {brand}")
        self.brand_preferences[brand] = list(preferences)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_currency(self, iso_code: str) -> Currency | None:
        """Return the Currency record for an ISO 4217 code, or None."""
        return _CURRENCIES_BY_ISO.get(iso_code.upper())

    def allowed_currencies(self, country_iso: str) -> frozenset[str]:
        """Return the country's hard allow-list of currency codes."""
        profile = _COUNTRY_PROFILES.get(country_iso.upper())
        if profile is None:
            return frozenset()
        return profile.allowed

    def default_currency(self, country_iso: str) -> str | None:
        """Return the country's default currency, or None if unconfigured."""
        profile = _COUNTRY_PROFILES.get(country_iso.upper())
        if profile is None:
            return None
        return profile.default

    def resolve_for_registration(
        self, brand: str, country_iso: str
    ) -> str | None:
        """Resolve the currency to assign a new registration.

        Algorithm:
          1. Load the country profile. If the country is unconfigured,
             return None -- the caller should reject the registration
             as "unsupported jurisdiction".
          2. Walk the brand's preference list in order. Return the
             first entry that is in the country's `allowed` set.
          3. If no brand preference matches, return the country's
             `default` currency.
          4. If the country has neither a default nor a brand-allowed
             currency, return None.
        """
        profile = _COUNTRY_PROFILES.get(country_iso.upper())
        if profile is None or not profile.allowed:
            return None

        prefs = self.brand_preferences.get(brand, [])
        for iso in prefs:
            if iso in profile.allowed:
                return iso

        return profile.default
