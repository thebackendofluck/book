# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Country reference data: names, filtering, and state/province lookup.

Python port of CountryService from the Scala production service
referenced in chapter 26. The responsibilities are:

1. **Registration-time country filtering** -- given a brand, return
   the list of countries from which a player may register. Countries
   that carry a LANDING or REGISTER block in the BlockedCountryService
   are excluded. Countries carrying only a LOGIN block still appear
   (the player can register, but will be blocked at login until they
   move); that is the weaker semantics the original Scala chose.
2. **Localized country names with English fallback** -- given an ISO
   country code and a locale, return the country's display name. If
   the locale is not supported for that country, fall back to English.
3. **State / province lookup for US and CA** -- these two countries
   require sub-national data for tax and licensing reasons. Other
   countries return an empty list.

The reference data is deliberately hard-coded in Python rather than
loaded from a database so that the module is self-contained and can
be run from a read-only filesystem. A production deployment would
swap `_COUNTRIES`, `_LOCALIZED_NAMES`, and `_SUBDIVISIONS` for rows
loaded from the operator's reference database.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blocked_country_service import BlockedCountryService, BlockType  # noqa: E402


@dataclass(frozen=True)
class Country:
    """A country reference record.

    `iso_code` is ISO 3166-1 alpha-2; `english_name` is the default
    display; `phone_prefix` is stored as a plain string (some countries
    have multi-digit or hyphenated prefixes that do not parse as int).
    """

    iso_code: str
    english_name: str
    phone_prefix: str


@dataclass(frozen=True)
class Subdivision:
    """A state / province / department record -- only populated for
    countries that the operator actually cares about at this level.
    """

    country_iso: str
    code: str  # state/province code, e.g. "NJ", "CA", "QC"
    name: str


# Representative subset of ISO 3166-1 alpha-2. A full list is ~250
# entries; the subset here covers every jurisdiction the book discusses
# plus the OECD footprint. Extend in production from a canonical source
# such as the Unicode CLDR territory list.
_COUNTRIES: tuple[Country, ...] = (
    Country("AR", "Argentina", "+54"),
    Country("AU", "Australia", "+61"),
    Country("AT", "Austria", "+43"),
    Country("BE", "Belgium", "+32"),
    Country("BR", "Brazil", "+55"),
    Country("CA", "Canada", "+1"),
    Country("CL", "Chile", "+56"),
    Country("CN", "China", "+86"),
    Country("CO", "Colombia", "+57"),
    Country("CZ", "Czech Republic", "+420"),
    Country("DK", "Denmark", "+45"),
    Country("FI", "Finland", "+358"),
    Country("FR", "France", "+33"),
    Country("DE", "Germany", "+49"),
    Country("GR", "Greece", "+30"),
    Country("IE", "Ireland", "+353"),
    Country("IT", "Italy", "+39"),
    Country("JP", "Japan", "+81"),
    Country("MX", "Mexico", "+52"),
    Country("MT", "Malta", "+356"),
    Country("NL", "Netherlands", "+31"),
    Country("NZ", "New Zealand", "+64"),
    Country("NO", "Norway", "+47"),
    Country("PE", "Peru", "+51"),
    Country("PH", "Philippines", "+63"),
    Country("PL", "Poland", "+48"),
    Country("PT", "Portugal", "+351"),
    Country("RO", "Romania", "+40"),
    Country("ZA", "South Africa", "+27"),
    Country("KR", "South Korea", "+82"),
    Country("ES", "Spain", "+34"),
    Country("SE", "Sweden", "+46"),
    Country("CH", "Switzerland", "+41"),
    Country("TR", "Turkey", "+90"),
    Country("AE", "United Arab Emirates", "+971"),
    Country("GB", "United Kingdom", "+44"),
    Country("US", "United States", "+1"),
)

_COUNTRIES_BY_ISO: dict[str, Country] = {c.iso_code: c for c in _COUNTRIES}

# Localized names: keyed by (locale, iso). Locale is a BCP 47 language
# tag. The English fallback is the Country.english_name field; this
# table only needs non-English overrides.
_LOCALIZED_NAMES: dict[tuple[str, str], str] = {
    # Portuguese (Brazil)
    ("pt-BR", "BR"): "Brasil",
    ("pt-BR", "US"): "Estados Unidos",
    ("pt-BR", "GB"): "Reino Unido",
    ("pt-BR", "ES"): "Espanha",
    ("pt-BR", "FR"): "França",
    ("pt-BR", "DE"): "Alemanha",
    ("pt-BR", "IT"): "Itália",
    ("pt-BR", "PT"): "Portugal",
    ("pt-BR", "CA"): "Canadá",
    ("pt-BR", "AR"): "Argentina",
    # Spanish
    ("es", "US"): "Estados Unidos",
    ("es", "GB"): "Reino Unido",
    ("es", "FR"): "Francia",
    ("es", "DE"): "Alemania",
    ("es", "IT"): "Italia",
    ("es", "BR"): "Brasil",
    # German
    ("de", "US"): "Vereinigte Staaten",
    ("de", "GB"): "Vereinigtes Königreich",
    ("de", "FR"): "Frankreich",
    ("de", "IT"): "Italien",
    ("de", "ES"): "Spanien",
    # French
    ("fr", "US"): "États-Unis",
    ("fr", "GB"): "Royaume-Uni",
    ("fr", "DE"): "Allemagne",
    ("fr", "IT"): "Italie",
    ("fr", "ES"): "Espagne",
}

# US states + DC + 5 permanently-inhabited territories
_US_SUBDIVISIONS: tuple[Subdivision, ...] = tuple(
    Subdivision("US", code, name)
    for code, name in (
        ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
        ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"),
        ("DE", "Delaware"), ("DC", "District of Columbia"), ("FL", "Florida"),
        ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"), ("IL", "Illinois"),
        ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"), ("KY", "Kentucky"),
        ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
        ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
        ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"),
        ("NE", "Nebraska"), ("NV", "Nevada"), ("NH", "New Hampshire"),
        ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
        ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
        ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"),
        ("RI", "Rhode Island"), ("SC", "South Carolina"), ("SD", "South Dakota"),
        ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"), ("VT", "Vermont"),
        ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"),
        ("WI", "Wisconsin"), ("WY", "Wyoming"),
        ("AS", "American Samoa"), ("GU", "Guam"), ("MP", "Northern Mariana Islands"),
        ("PR", "Puerto Rico"), ("VI", "U.S. Virgin Islands"),
    )
)

# Canadian provinces and territories
_CA_SUBDIVISIONS: tuple[Subdivision, ...] = tuple(
    Subdivision("CA", code, name)
    for code, name in (
        ("AB", "Alberta"), ("BC", "British Columbia"), ("MB", "Manitoba"),
        ("NB", "New Brunswick"), ("NL", "Newfoundland and Labrador"),
        ("NS", "Nova Scotia"), ("NT", "Northwest Territories"),
        ("NU", "Nunavut"), ("ON", "Ontario"), ("PE", "Prince Edward Island"),
        ("QC", "Quebec"), ("SK", "Saskatchewan"), ("YT", "Yukon"),
    )
)

_SUBDIVISIONS_BY_COUNTRY: dict[str, tuple[Subdivision, ...]] = {
    "US": _US_SUBDIVISIONS,
    "CA": _CA_SUBDIVISIONS,
}


@dataclass
class CountryService:
    """Reference data service backed by the in-module constants above."""

    blocked: BlockedCountryService

    def get_country(self, iso_code: str) -> Country | None:
        """Return the Country record for an ISO code, or None."""
        return _COUNTRIES_BY_ISO.get(iso_code.upper())

    def list_countries(self, *, locale: str | None = None) -> list[tuple[str, str]]:
        """Return all countries as (iso_code, localised_name) pairs.

        Sorted by the localised name in the requested locale (falling
        back to English), the same ordering the original service used
        to render dropdowns in the sign-up flow.
        """
        pairs = [
            (c.iso_code, self._localized_name(c, locale))
            for c in _COUNTRIES
        ]
        pairs.sort(key=lambda pair: pair[1])
        return pairs

    def list_countries_for_registration(
        self, brand: str, *, locale: str | None = None
    ) -> list[tuple[str, str]]:
        """Return (iso_code, localised_name) for every country from
        which a player may register for the given brand.

        Countries carrying a LANDING or REGISTER block are dropped.
        LOGIN-only blocks are tolerated (the player can register, but
        will be blocked at login until they move) so that the consent
        flow remains symmetric with the original Scala semantics.
        """
        result: list[tuple[str, str]] = []
        for c in _COUNTRIES:
            active = self.blocked.find_block_for_country(brand, c.iso_code)
            if active is None or active == BlockType.LOGIN:
                result.append((c.iso_code, self._localized_name(c, locale)))
        result.sort(key=lambda pair: pair[1])
        return result

    def subdivisions(self, iso_code: str) -> list[Subdivision]:
        """Return the list of states/provinces for a country, or [] if
        the operator has not configured sub-national data for it.
        """
        return list(_SUBDIVISIONS_BY_COUNTRY.get(iso_code.upper(), ()))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _localized_name(self, country: Country, locale: str | None) -> str:
        if locale is None:
            return country.english_name
        # Try exact locale (e.g. "pt-BR")
        name = _LOCALIZED_NAMES.get((locale, country.iso_code))
        if name is not None:
            return name
        # Try the language prefix (e.g. "pt" from "pt-BR")
        if "-" in locale:
            lang = locale.split("-", 1)[0]
            name = _LOCALIZED_NAMES.get((lang, country.iso_code))
            if name is not None:
                return name
        return country.english_name
