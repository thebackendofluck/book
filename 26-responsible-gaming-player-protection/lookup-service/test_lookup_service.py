# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Unit tests for the chapter-26 lookup service.

Run with:
    python -m pytest writing/new-book/scripts/chapter-26/lookup-service/

Or directly:
    python writing/new-book/scripts/chapter-26/lookup-service/test_lookup_service.py

The tests use pytest when available and fall back to a tiny hand-rolled
runner so the file is runnable in a clean virtualenv without extra
dependencies.
"""

from __future__ import annotations

import ipaddress
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blocked_country_service import BlockedCountryService, BlockType  # noqa: E402
from country_service import CountryService, Subdivision  # noqa: E402
from currency_service import CurrencyService  # noqa: E402
from geo_ip_database import (  # noqa: E402
    in_memory_geo_ip_database,
    null_geo_ip_database,
)


class GeoIpDatabaseTests(unittest.TestCase):
    def test_known_ip_returns_iso_code(self) -> None:
        db = in_memory_geo_ip_database({"8.8.8.8": "US", "1.1.1.1": "AU"})
        self.assertEqual(db.country_code_for("8.8.8.8"), "US")
        self.assertEqual(db.country_code_for("1.1.1.1"), "AU")

    def test_unknown_ip_returns_none(self) -> None:
        db = in_memory_geo_ip_database({"8.8.8.8": "US"})
        self.assertIsNone(db.country_code_for("203.0.113.42"))

    def test_private_ip_returns_none(self) -> None:
        """RFC 1918 addresses must never resolve to a country."""
        db = in_memory_geo_ip_database({"10.0.0.1": "ZZ"})
        self.assertIsNone(db.country_code_for("10.0.0.1"))

    def test_loopback_ip_returns_none(self) -> None:
        db = in_memory_geo_ip_database({})
        self.assertIsNone(db.country_code_for("127.0.0.1"))

    def test_invalid_ip_string_returns_none(self) -> None:
        db = in_memory_geo_ip_database({})
        self.assertIsNone(db.country_code_for("not-an-ip"))

    def test_accepts_ipaddress_objects(self) -> None:
        db = in_memory_geo_ip_database({"8.8.8.8": "US"})
        addr = ipaddress.IPv4Address("8.8.8.8")
        self.assertEqual(db.country_code_for(addr), "US")

    def test_null_database_returns_none_for_every_ip(self) -> None:
        db = null_geo_ip_database()
        for ip in ("8.8.8.8", "1.1.1.1", "203.0.113.5"):
            self.assertIsNone(db.country_code_for(ip))


class BlockedCountryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geo = in_memory_geo_ip_database(
            {"8.8.8.8": "US", "1.1.1.1": "AU", "195.95.193.1": "FR"}
        )
        self.svc = BlockedCountryService(geo_ip=self.geo)

    def test_country_without_block_returns_none(self) -> None:
        self.assertIsNone(self.svc.find_block_for_country("brand-x", "FR"))

    def test_single_block_type(self) -> None:
        self.svc.add_block("brand-x", "US", BlockType.REGISTER)
        self.assertEqual(
            self.svc.find_block_for_country("brand-x", "US"), BlockType.REGISTER
        )

    def test_strictest_block_wins(self) -> None:
        """If a country carries LANDING, REGISTER and LOGIN, the API returns LOGIN."""
        self.svc.add_block("brand-x", "US", {BlockType.LANDING, BlockType.REGISTER})
        self.svc.add_block("brand-x", "US", BlockType.LOGIN)
        self.assertEqual(
            self.svc.find_block_for_country("brand-x", "US"), BlockType.LOGIN
        )

    def test_block_is_brand_scoped(self) -> None:
        self.svc.add_block("brand-x", "US", BlockType.LOGIN)
        self.assertIsNone(self.svc.find_block_for_country("brand-y", "US"))

    def test_country_code_normalised_to_uppercase(self) -> None:
        self.svc.add_block("brand-x", "us", BlockType.REGISTER)
        self.assertEqual(
            self.svc.find_block_for_country("brand-x", "US"), BlockType.REGISTER
        )

    def test_ip_based_lookup(self) -> None:
        self.svc.add_block("brand-x", "US", BlockType.LOGIN)
        self.assertEqual(
            self.svc.find_block_for_country_using_ip("brand-x", "8.8.8.8"),
            BlockType.LOGIN,
        )

    def test_ip_based_lookup_unknown_ip_returns_none(self) -> None:
        """Fail-open when the IP cannot be resolved."""
        self.svc.add_block("brand-x", "US", BlockType.LOGIN)
        self.assertIsNone(
            self.svc.find_block_for_country_using_ip("brand-x", "203.0.113.5")
        )

    def test_is_country_blocked_respects_escalation(self) -> None:
        self.svc.add_block("brand-x", "US", BlockType.LOGIN)
        # A LOGIN block must also satisfy a REGISTER check
        self.assertTrue(self.svc.is_country_blocked("brand-x", "US", BlockType.REGISTER))
        self.assertTrue(self.svc.is_country_blocked("brand-x", "US", BlockType.LANDING))
        self.assertTrue(self.svc.is_country_blocked("brand-x", "US", BlockType.LOGIN))

    def test_remove_specific_block_type(self) -> None:
        self.svc.add_block("brand-x", "US", {BlockType.REGISTER, BlockType.LOGIN})
        self.svc.remove_block("brand-x", "US", block_type=BlockType.LOGIN)
        self.assertEqual(
            self.svc.find_block_for_country("brand-x", "US"), BlockType.REGISTER
        )


class CountryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geo = null_geo_ip_database()
        self.blocked = BlockedCountryService(geo_ip=self.geo)
        self.svc = CountryService(blocked=self.blocked)

    def test_get_country_english(self) -> None:
        country = self.svc.get_country("US")
        self.assertIsNotNone(country)
        if country is not None:
            self.assertEqual(country.english_name, "United States")

    def test_localized_name_with_exact_locale(self) -> None:
        countries = dict(self.svc.list_countries(locale="pt-BR"))
        self.assertEqual(countries["BR"], "Brasil")
        self.assertEqual(countries["US"], "Estados Unidos")

    def test_localized_name_with_language_prefix_fallback(self) -> None:
        """A locale of "es-MX" should fall back to the "es" entries."""
        countries = dict(self.svc.list_countries(locale="es-MX"))
        self.assertEqual(countries["US"], "Estados Unidos")

    def test_localized_name_falls_back_to_english(self) -> None:
        """A country with no translation in the requested locale returns English."""
        countries = dict(self.svc.list_countries(locale="de"))
        # Japan has no German override in our table
        self.assertEqual(countries["JP"], "Japan")

    def test_registration_filter_drops_register_blocks(self) -> None:
        self.blocked.add_block("brand-x", "US", BlockType.REGISTER)
        iso_codes = {iso for iso, _ in self.svc.list_countries_for_registration("brand-x")}
        self.assertNotIn("US", iso_codes)
        self.assertIn("CA", iso_codes)

    def test_registration_filter_keeps_login_only_blocks(self) -> None:
        """LOGIN blocks do not prevent registration (documented behaviour)."""
        self.blocked.add_block("brand-x", "US", BlockType.LOGIN)
        iso_codes = {iso for iso, _ in self.svc.list_countries_for_registration("brand-x")}
        self.assertIn("US", iso_codes)

    def test_us_subdivisions_returned(self) -> None:
        subs = self.svc.subdivisions("US")
        self.assertGreaterEqual(len(subs), 50)
        codes = {s.code for s in subs}
        for expected in ("NJ", "CA", "TX", "PR"):
            self.assertIn(expected, codes)

    def test_canada_subdivisions_returned(self) -> None:
        subs = self.svc.subdivisions("CA")
        codes = {s.code for s in subs}
        for expected in ("QC", "ON", "BC", "YT"):
            self.assertIn(expected, codes)

    def test_country_without_subdivision_returns_empty(self) -> None:
        self.assertEqual(self.svc.subdivisions("BR"), [])


class CurrencyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = CurrencyService()
        self.svc.register_brand("brand-eu", ["EUR", "GBP"])
        self.svc.register_brand("brand-latam", ["USD", "BRL", "MXN"])
        self.svc.register_brand("brand-global", ["USD", "EUR", "GBP", "BRL"])

    def test_resolve_for_registration_brand_preference_wins(self) -> None:
        """A brand preference that is in the country's allow-list wins."""
        self.assertEqual(self.svc.resolve_for_registration("brand-eu", "DE"), "EUR")

    def test_resolve_for_registration_falls_back_to_country_default(self) -> None:
        """brand-eu does not list BRL but BR is restricted to BRL, so default wins."""
        self.assertEqual(self.svc.resolve_for_registration("brand-eu", "BR"), "BRL")

    def test_resolve_for_registration_brazil_is_brl_only(self) -> None:
        """Brazil is a BRL-only jurisdiction; USD/EUR must not be offered there."""
        self.assertEqual(self.svc.resolve_for_registration("brand-latam", "BR"), "BRL")
        self.assertNotIn("USD", self.svc.allowed_currencies("BR"))

    def test_resolve_for_registration_uk_skips_blocked_usd(self) -> None:
        """brand-global has USD first but the UK allow-list blocks USD, so
        the next entry in the preference list (EUR) wins because EUR is
        in the UK allow-list. GBP would only be returned if the brand did
        not list EUR before it.
        """
        self.assertEqual(self.svc.resolve_for_registration("brand-global", "GB"), "EUR")

    def test_resolve_for_registration_uk_gbp_only_brand(self) -> None:
        """A brand that lists only GBP (and blocked entries) gets GBP."""
        svc = CurrencyService()
        svc.register_brand("gbp-only", ["USD", "GBP"])
        self.assertEqual(svc.resolve_for_registration("gbp-only", "GB"), "GBP")

    def test_resolve_for_registration_unknown_country_returns_none(self) -> None:
        self.assertIsNone(self.svc.resolve_for_registration("brand-eu", "ZZ"))

    def test_register_brand_rejects_unknown_currency(self) -> None:
        with self.assertRaises(ValueError):
            self.svc.register_brand("bad-brand", ["FAKE"])

    def test_register_brand_rejects_empty_preferences(self) -> None:
        with self.assertRaises(ValueError):
            self.svc.register_brand("bad-brand", [])


if __name__ == "__main__":
    unittest.main()
