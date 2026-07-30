# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Per-brand geo-blocking with IP-based enforcement.

Python port of BlockedCountryService from the Scala production service
referenced in chapter 26. The service answers two questions:

1. Given a brand and a country code, is that country blocked for the
   brand and, if so, which block type applies (Landing, Register,
   Login)?
2. Given a brand and a player IP, which block type applies after
   resolving the IP to a country via GeoIP?

Block types are ordered by escalating strictness:

- **LANDING**  -- the country is blocked from seeing the marketing
                  site at all. Requests are redirected to a "not
                  available in your jurisdiction" page before any
                  platform code runs.
- **REGISTER** -- existing players from that country can still log in,
                  but new registrations are rejected. This is the
                  common state during a regulatory transition when
                  the operator is winding down in a market.
- **LOGIN**    -- no player from that country can sign in at all; the
                  account is effectively frozen until the player
                  travels to a permitted jurisdiction. Usually paired
                  with a withdrawals-only mode in the wallet service.

A single (brand, country) pair can carry any subset of the three. The
`findBlockForCountryUsingIp` method returns the **most restrictive**
block that applies, following the order Login > Register > Landing.
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Directory name has a hyphen so it is not a valid Python package name;
# fall back to a sys.path insertion for the sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geo_ip_database import GeoIpDatabase  # noqa: E402


class BlockType(enum.IntEnum):
    """Block categories, ordered by escalating strictness.

    IntEnum so that we can compare with `max()` and pick the strongest
    block in one call.
    """

    LANDING = 1
    REGISTER = 2
    LOGIN = 3


@dataclass(frozen=True)
class CountryBlock:
    """A single block entry: brand + country + the set of block types."""

    brand: str
    country_code: str  # ISO 3166-1 alpha-2
    block_types: frozenset[BlockType]

    def __post_init__(self) -> None:
        # Normalise country code to uppercase so lookups are case-insensitive
        object.__setattr__(self, "country_code", self.country_code.upper())


@dataclass
class BlockedCountryService:
    """Brand-scoped country block database with GeoIP integration."""

    geo_ip: GeoIpDatabase
    _blocks: dict[tuple[str, str], CountryBlock] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_block(
        self,
        brand: str,
        country_code: str,
        block_types: "BlockType | frozenset[BlockType] | set[BlockType]",
    ) -> None:
        """Register a block. Merges with any existing entry for the
        same (brand, country) pair -- block types are a set union so
        calling `add_block(..., LANDING)` and then `add_block(...,
        REGISTER)` ends up with both flags set.
        """
        country_code = country_code.upper()
        key = (brand, country_code)
        if isinstance(block_types, BlockType):
            incoming: frozenset[BlockType] = frozenset({block_types})
        else:
            incoming = frozenset(block_types)
        existing = self._blocks.get(key)
        if existing is not None:
            merged = existing.block_types | incoming
        else:
            merged = incoming
        self._blocks[key] = CountryBlock(
            brand=brand, country_code=country_code, block_types=merged
        )

    def remove_block(
        self, brand: str, country_code: str, *, block_type: BlockType | None = None
    ) -> None:
        """Remove either a specific block type or every block for the
        (brand, country) pair when `block_type` is None.
        """
        key = (brand, country_code.upper())
        if block_type is None:
            self._blocks.pop(key, None)
            return
        existing = self._blocks.get(key)
        if existing is None:
            return
        remaining = existing.block_types - {block_type}
        if not remaining:
            self._blocks.pop(key, None)
        else:
            self._blocks[key] = CountryBlock(
                brand=existing.brand,
                country_code=existing.country_code,
                block_types=remaining,
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def find_block_for_country(
        self, brand: str, country_code: str
    ) -> BlockType | None:
        """Return the strongest block type applied to the (brand, country)
        pair, or None if the country is permitted for the brand.
        """
        block = self._blocks.get((brand, country_code.upper()))
        if block is None or not block.block_types:
            return None
        return max(block.block_types)

    def find_block_for_country_using_ip(
        self,
        brand: str,
        ip: str,
    ) -> BlockType | None:
        """Resolve the IP to a country via GeoIP, then consult the block
        database. Returns None when the IP cannot be resolved -- this is
        the "fail open" default documented in chapter 26; fail-closed
        would lock out every player whose IP is missing from the MaxMind
        database (including most mobile carriers in edge cases), which
        is an unacceptable false-positive rate for a consumer product.
        """
        country = self.geo_ip.country_code_for(ip)
        if country is None:
            return None
        return self.find_block_for_country(brand, country)

    def is_country_blocked(
        self,
        brand: str,
        country_code: str,
        block_type: BlockType,
    ) -> bool:
        """True if the specific block type or a stricter one applies.

        This is the method the registration endpoint calls to answer
        "is this country blocked from registering right now?" -- and it
        treats a LOGIN block as implying a REGISTER block too.
        """
        active = self.find_block_for_country(brand, country_code)
        if active is None:
            return False
        return active >= block_type

    def list_blocks_for_brand(self, brand: str) -> list[CountryBlock]:
        """Return every block registered for a brand, ordered by
        country code. Useful for compliance exports.
        """
        return sorted(
            (b for (b_brand, _), b in self._blocks.items() if b_brand == brand),
            key=lambda b: b.country_code,
        )
