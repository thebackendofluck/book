# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""sms_10dlc.py — US A2P 10DLC registration helper and route selection.

US application-to-person SMS must run over registered 10-digit long codes
(10DLC), a toll-free verified number, or a short code. This module models the
brand/campaign registration payloads and picks an outbound route by
destination and message class. Companion module for Chapter 33c.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MessageClass(str, Enum):
    OTP = "otp"  # 2FA / login codes
    TRANSACTIONAL = "transactional"  # receipts, withdrawal status
    MARKETING = "marketing"  # promos (requires explicit opt-in)


class Route(str, Enum):
    TEN_DLC = "10dlc"
    TOLL_FREE = "toll_free"
    SHORT_CODE = "short_code"


@dataclass(frozen=True)
class BrandRegistration:
    legal_name: str
    ein: str  # US tax id
    country: str
    vertical: str  # e.g. "GAMBLING"


@dataclass(frozen=True)
class CampaignRegistration:
    brand: BrandRegistration
    use_case: str  # e.g. "2FA", "ACCOUNT_NOTIFICATION", "MARKETING"
    sample_messages: tuple[str, ...]
    opt_in_description: str


def brand_payload(reg: BrandRegistration) -> dict[str, str]:
    return {
        "displayName": reg.legal_name,
        "ein": reg.ein,
        "country": reg.country,
        "vertical": reg.vertical,
        "entityType": "PRIVATE_PROFIT",
    }


def campaign_payload(reg: CampaignRegistration) -> dict[str, object]:
    return {
        "usecase": reg.use_case,
        "sampleMessages": list(reg.sample_messages),
        "optinKeywords": ["START", "YES"],
        "optoutKeywords": ["STOP"],
        "helpKeywords": ["HELP"],
        "description": reg.opt_in_description,
    }


def select_route(destination_e164: str, message_class: MessageClass) -> Route:
    """Pick the outbound route.

    Non-US destinations do not use 10DLC. Inside the US, OTP/marketing traffic
    prefers a short code when available; transactional rides 10DLC.
    """
    if not destination_e164.startswith("+1"):
        return Route.TOLL_FREE  # international A2P via toll-free / global route
    if message_class in (MessageClass.OTP, MessageClass.MARKETING):
        return Route.SHORT_CODE
    return Route.TEN_DLC
