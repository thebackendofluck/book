#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Self-Exclusion Service with National Registry Integration

Implements player self-exclusion with integration to national registries
(GAMSTOP UK, Cruks Netherlands, Spelpaus Sweden, OASIS Germany).

Features:
- Multi-jurisdiction self-exclusion enforcement
- National registry sync (pull and push)
- Cooldown period management
- Mandatory account closure workflow
- Audit trail for regulatory compliance
- Re-activation process with mandatory cooling-off

Usage:
    # As a module
    from self_exclusion_service import SelfExclusionService
    service = SelfExclusionService()
    service.exclude_player("player-123", duration_months=6, jurisdiction="uk")

    # CLI demo
    python3 self_exclusion_service.py --demo
"""

import json
import hashlib
import logging
import argparse
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and constants
# ---------------------------------------------------------------------------

class ExclusionStatus(Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"          # Admin override (regulatory only)
    PENDING_REACTIVATION = "pending_reactivation"


class ExclusionType(Enum):
    SELF_EXCLUSION = "self_exclusion"        # Player-initiated
    THIRD_PARTY = "third_party"             # Family/friend request
    REGULATORY = "regulatory"               # Regulator-imposed
    OPERATOR_INITIATED = "operator_initiated"  # Operator concern


class ExclusionDuration(Enum):
    SIX_MONTHS = 6
    ONE_YEAR = 12
    TWO_YEARS = 24
    FIVE_YEARS = 60
    PERMANENT = 0  # 0 = indefinite


# Jurisdiction-specific configuration
JURISDICTION_CONFIG = {
    "uk": {
        "registry": "GAMSTOP",
        "api_endpoint": "https://api.gamstop.co.uk/v2",
        "min_duration_months": 6,
        "max_duration_months": 0,  # 0 = unlimited/permanent option available
        "cooling_off_days": 1,     # 24-hour cooling-off before exclusion takes effect
        "reactivation_wait_days": 1,  # 24 hours after exclusion end before reactivation
        "mandatory_check_frequency": "on_every_login",
        "supports_temporary": True,
        "supports_permanent": True,
        "required_fields": ["first_name", "last_name", "date_of_birth", "postcode", "email"],
        "notification_required": True,
        "close_account_immediately": True,
        "refund_balance": True,
        "void_pending_bets": True,
        "remove_from_marketing": True,
    },
    "malta": {
        "registry": "MGA Self-Exclusion Database",
        "api_endpoint": "https://api.mga.org.mt/exclusion/v1",
        "min_duration_months": 6,
        "max_duration_months": 0,
        "cooling_off_days": 0,
        "reactivation_wait_days": 7,
        "mandatory_check_frequency": "on_every_login",
        "supports_temporary": True,
        "supports_permanent": True,
        "required_fields": ["first_name", "last_name", "date_of_birth", "nationality"],
        "notification_required": True,
        "close_account_immediately": True,
        "refund_balance": True,
        "void_pending_bets": True,
        "remove_from_marketing": True,
    },
    "sweden": {
        "registry": "Spelpaus",
        "api_endpoint": "https://api.spelpaus.se/v1",
        "min_duration_months": 1,
        "max_duration_months": 0,
        "cooling_off_days": 0,  # Immediate in Sweden
        "reactivation_wait_days": 0,  # Auto-reactivation after period
        "mandatory_check_frequency": "on_every_login",
        "supports_temporary": True,
        "supports_permanent": True,
        "required_fields": ["personnummer"],  # Swedish personal ID number
        "notification_required": False,  # Spelpaus handles notifications
        "close_account_immediately": True,
        "refund_balance": True,
        "void_pending_bets": True,
        "remove_from_marketing": True,
    },
    "ontario": {
        "registry": "Ontario iGaming Self-Exclusion",
        "api_endpoint": "https://api.igamingontario.ca/exclusion/v1",
        "min_duration_months": 3,
        "max_duration_months": 0,
        "cooling_off_days": 1,
        "reactivation_wait_days": 30,
        "mandatory_check_frequency": "on_every_login",
        "supports_temporary": True,
        "supports_permanent": True,
        "required_fields": ["first_name", "last_name", "date_of_birth", "ontario_id"],
        "notification_required": True,
        "close_account_immediately": True,
        "refund_balance": True,
        "void_pending_bets": True,
        "remove_from_marketing": True,
    },
    "germany": {
        "registry": "OASIS",
        "api_endpoint": "https://oasis.ggl.de/api/v1",
        "min_duration_months": 3,
        "max_duration_months": 0,
        "cooling_off_days": 0,
        "reactivation_wait_days": 90,  # 3 months mandatory wait in Germany
        "mandatory_check_frequency": "on_every_login",
        "supports_temporary": True,
        "supports_permanent": True,
        "required_fields": ["vorname", "nachname", "geburtsdatum", "personalausweis"],
        "notification_required": True,
        "close_account_immediately": True,
        "refund_balance": True,
        "void_pending_bets": True,
        "remove_from_marketing": True,
    },
}


@dataclass
class ExclusionRecord:
    """A single self-exclusion record."""
    exclusion_id: str
    player_id: str
    player_hash: str              # Hashed PII for registry matching
    exclusion_type: str
    status: str
    jurisdiction: str
    registry_name: str
    registry_reference: Optional[str]  # Reference from national registry
    duration_months: int
    start_date: str
    end_date: Optional[str]
    requested_at: str
    effective_at: str              # After cooling-off period
    account_closed_at: Optional[str]
    balance_refunded: float
    pending_bets_voided: int
    marketing_removed: bool
    reactivation_eligible_date: Optional[str]
    audit_trail: list = field(default_factory=list)


@dataclass
class RegistryCheckResult:
    """Result of checking a player against national exclusion registries."""
    player_id: str
    checked_at: str
    registries_checked: list
    is_excluded: bool
    exclusion_details: Optional[dict] = None


# ---------------------------------------------------------------------------
# Self-Exclusion Service
# ---------------------------------------------------------------------------

class SelfExclusionService:
    """
    Manages player self-exclusion with multi-jurisdiction support
    and national registry integration.
    """

    def __init__(self):
        # In production, these would be backed by PostgreSQL and Redis
        self._exclusions: dict = {}          # exclusion_id -> ExclusionRecord
        self._player_exclusions: dict = defaultdict(list)  # player_id -> [exclusion_ids]
        self._registry_cache: dict = {}      # player_hash -> RegistryCheckResult

    def exclude_player(
        self,
        player_id: str,
        duration_months: int,
        jurisdiction: str,
        exclusion_type: str = ExclusionType.SELF_EXCLUSION.value,
        player_pii: Optional[dict] = None,
        reason: Optional[str] = None,
    ) -> ExclusionRecord:
        """
        Create a self-exclusion for a player.

        This is the primary entry point. It:
        1. Validates the request against jurisdiction rules
        2. Creates the exclusion record
        3. Registers with national registry
        4. Closes the account immediately
        5. Refunds balance and voids pending bets
        6. Removes from all marketing
        """
        config = JURISDICTION_CONFIG.get(jurisdiction)
        if not config:
            raise ValueError(f"Unsupported jurisdiction: {jurisdiction}. "
                             f"Supported: {list(JURISDICTION_CONFIG.keys())}")

        # Validate duration
        if duration_months != 0 and duration_months < config["min_duration_months"]:  # ty:ignore[unsupported-operator]
            raise ValueError(
                f"Minimum exclusion duration for {jurisdiction} is "
                f"{config['min_duration_months']} months"
            )

        # Check if player already excluded
        active_exclusions = self._get_active_exclusions(player_id)
        if active_exclusions:
            logger.warning(f"Player {player_id} already has active exclusion(s)")
            # In most jurisdictions, new exclusion extends the period
            for exc in active_exclusions:
                logger.info(f"  Existing: {exc.exclusion_id} until {exc.end_date}")

        now = datetime.now(timezone.utc)
        exclusion_id = f"exc-{uuid.uuid4().hex[:12]}"

        # Calculate effective date (after cooling-off)
        cooling_off_days = config["cooling_off_days"]
        effective_date = now + timedelta(days=cooling_off_days)  # ty:ignore[invalid-argument-type]

        # Calculate end date
        if duration_months == 0:
            end_date = None  # Permanent
            reactivation_date = None
        else:
            end_date = effective_date + timedelta(days=duration_months * 30)
            reactivation_date = end_date + timedelta(days=config["reactivation_wait_days"])  # ty:ignore[invalid-argument-type]

        # Hash PII for registry matching
        player_hash = self._hash_player_pii(player_pii) if player_pii else f"hash-{player_id}"

        # Register with national registry
        registry_ref = self._register_with_national_registry(
            jurisdiction, player_hash, player_pii, duration_months
        )

        # Process account closure
        balance_refunded = self._refund_player_balance(player_id)
        bets_voided = self._void_pending_bets(player_id)
        self._remove_from_marketing(player_id)
        self._close_account(player_id)

        # Create record
        record = ExclusionRecord(
            exclusion_id=exclusion_id,
            player_id=player_id,
            player_hash=player_hash,
            exclusion_type=exclusion_type,
            status=ExclusionStatus.ACTIVE.value,
            jurisdiction=jurisdiction,
            registry_name=config["registry"],  # ty:ignore[invalid-argument-type]
            registry_reference=registry_ref,
            duration_months=duration_months,
            start_date=now.isoformat(),
            end_date=end_date.isoformat() if end_date else None,
            requested_at=now.isoformat(),
            effective_at=effective_date.isoformat(),
            account_closed_at=now.isoformat(),
            balance_refunded=balance_refunded,
            pending_bets_voided=bets_voided,
            marketing_removed=True,
            reactivation_eligible_date=reactivation_date.isoformat() if reactivation_date else None,
            audit_trail=[
                {
                    "timestamp": now.isoformat(),
                    "action": "EXCLUSION_CREATED",
                    "details": f"Type: {exclusion_type}, Duration: {duration_months}m, "
                               f"Jurisdiction: {jurisdiction}",
                    "reason": reason or "Player self-exclusion request",
                }
            ],
        )

        self._exclusions[exclusion_id] = record
        self._player_exclusions[player_id].append(exclusion_id)

        logger.info(f"Self-exclusion created: {exclusion_id}")
        logger.info(f"  Player: {player_id}")
        logger.info(f"  Registry: {config['registry']} (ref: {registry_ref})")
        logger.info(f"  Duration: {'Permanent' if duration_months == 0 else f'{duration_months} months'}")
        logger.info(f"  Effective: {effective_date.isoformat()}")
        logger.info(f"  Balance refunded: {balance_refunded:.2f}")
        logger.info(f"  Bets voided: {bets_voided}")

        return record

    def check_player_exclusion(self, player_id: str, jurisdiction: str) -> RegistryCheckResult:
        """
        Check if a player is excluded. Called on EVERY login and before
        any gambling activity.

        This checks both:
        1. Local exclusion database
        2. National registry (with caching)
        """
        now = datetime.now(timezone.utc)

        # Check local database first (fastest)
        local_exclusions = self._get_active_exclusions(player_id)
        if local_exclusions:
            result = RegistryCheckResult(
                player_id=player_id,
                checked_at=now.isoformat(),
                registries_checked=["local"],
                is_excluded=True,
                exclusion_details={
                    "source": "local_database",
                    "exclusion_id": local_exclusions[0].exclusion_id,
                    "end_date": local_exclusions[0].end_date,
                    "registry": local_exclusions[0].registry_name,
                },
            )
            logger.info(f"Player {player_id} is EXCLUDED (local database)")
            return result

        # Check national registry
        config = JURISDICTION_CONFIG.get(jurisdiction)
        if config:
            registry_excluded = self._check_national_registry(
                jurisdiction, player_id
            )
            if registry_excluded:
                result = RegistryCheckResult(
                    player_id=player_id,
                    checked_at=now.isoformat(),
                    registries_checked=["local", config["registry"]],
                    is_excluded=True,
                    exclusion_details={
                        "source": config["registry"],
                        "message": "Player found in national exclusion registry",
                    },
                )
                logger.warning(f"Player {player_id} is EXCLUDED ({config['registry']})")
                return result

        result = RegistryCheckResult(
            player_id=player_id,
            checked_at=now.isoformat(),
            registries_checked=["local"] + ([config["registry"]] if config else []),
            is_excluded=False,
        )
        return result

    def request_reactivation(self, player_id: str, exclusion_id: str) -> dict:
        """
        Process a player reactivation request after exclusion period ends.

        Reactivation is NOT automatic in most jurisdictions. The player must:
        1. Wait for the exclusion period to end
        2. Wait for the mandatory reactivation cooling-off period
        3. Explicitly request reactivation
        4. Complete a responsible gambling assessment
        """
        record = self._exclusions.get(exclusion_id)
        if not record:
            raise ValueError(f"Exclusion {exclusion_id} not found")

        if record.player_id != player_id:
            raise ValueError("Player ID mismatch")

        now = datetime.now(timezone.utc)

        # Check if exclusion period has ended
        if record.duration_months == 0:
            return {
                "status": "DENIED",
                "reason": "Permanent exclusion cannot be reactivated",
            }

        if record.end_date:
            end = datetime.fromisoformat(record.end_date)
            if now < end:
                remaining = (end - now).days
                return {
                    "status": "DENIED",
                    "reason": f"Exclusion period has not ended. {remaining} days remaining.",
                }

        # Check reactivation eligibility date
        if record.reactivation_eligible_date:
            eligible = datetime.fromisoformat(record.reactivation_eligible_date)
            if now < eligible:
                wait_days = (eligible - now).days
                return {
                    "status": "DENIED",
                    "reason": f"Mandatory cooling-off period. Eligible in {wait_days} days.",
                }

        # Mark as pending reactivation
        record.status = ExclusionStatus.PENDING_REACTIVATION.value
        record.audit_trail.append({
            "timestamp": now.isoformat(),
            "action": "REACTIVATION_REQUESTED",
            "details": "Player requested reactivation after exclusion period ended",
        })

        logger.info(f"Reactivation requested for {player_id} (exclusion: {exclusion_id})")

        return {
            "status": "PENDING",
            "message": "Reactivation request submitted. You must complete a responsible "
                       "gambling assessment before your account can be reactivated.",
            "next_steps": [
                "Complete responsible gambling self-assessment questionnaire",
                "Set new deposit limits (mandatory after reactivation)",
                "Account review by compliance team (24-48 hours)",
            ],
        }

    def get_exclusion_history(self, player_id: str) -> list:
        """Get full exclusion history for a player (compliance reporting)."""
        exclusion_ids = self._player_exclusions.get(player_id, [])
        return [asdict(self._exclusions[eid]) for eid in exclusion_ids
                if eid in self._exclusions]

    # -----------------------------------------------------------------------
    # Private methods
    # -----------------------------------------------------------------------

    def _get_active_exclusions(self, player_id: str) -> list:
        """Get all active exclusions for a player."""
        exclusion_ids = self._player_exclusions.get(player_id, [])
        active = []
        now = datetime.now(timezone.utc)

        for eid in exclusion_ids:
            record = self._exclusions.get(eid)
            if not record:
                continue

            if record.status != ExclusionStatus.ACTIVE.value:
                continue

            # Check if time-limited exclusion has expired
            if record.end_date:
                end = datetime.fromisoformat(record.end_date)
                if now > end:
                    record.status = ExclusionStatus.EXPIRED.value
                    record.audit_trail.append({
                        "timestamp": now.isoformat(),
                        "action": "EXCLUSION_EXPIRED",
                        "details": "Exclusion period ended naturally",
                    })
                    continue

            active.append(record)

        return active

    def _hash_player_pii(self, pii: dict) -> str:
        """Create a consistent hash of player PII for registry matching."""
        normalized = json.dumps(
            {k: v.strip().lower() for k, v in sorted(pii.items())},
            sort_keys=True,
        )
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _register_with_national_registry(
        self, jurisdiction: str, player_hash: str,
        player_pii: Optional[dict], duration_months: int
    ) -> Optional[str]:
        """
        Register the exclusion with the national registry.

        In production, this would make an API call to GAMSTOP/Spelpaus/OASIS.
        Here we simulate the registration and return a reference number.
        """
        config = JURISDICTION_CONFIG.get(jurisdiction)
        if not config:
            return None

        registry = config["registry"]
        logger.info(f"Registering with {registry} at {config['api_endpoint']}")

        # Simulate API call - in production, use httpx/aiohttp
        # POST config["api_endpoint"]/register
        # Body: { player_hash, duration, operator_id, ... }
        # Response: { reference_id, status }

        reference = f"{registry.upper().replace(' ', '-')}-{uuid.uuid4().hex[:8].upper()}"  # ty:ignore[possibly-missing-attribute]
        logger.info(f"  Registry reference: {reference}")

        return reference

    def _check_national_registry(self, jurisdiction: str, player_id: str) -> bool:
        """
        Check if a player is on the national exclusion registry.

        In production, this is called on every login. Results are cached
        for a short period (typically 5-15 minutes) to reduce API calls.
        """
        config = JURISDICTION_CONFIG.get(jurisdiction)
        if not config:
            return False

        # Simulate registry check - in production, call registry API
        # GET config["api_endpoint"]/check
        # Body: { player_hash }
        # Response: { is_excluded, details }

        # Check cache first
        cache_key = f"{jurisdiction}:{player_id}"
        cached = self._registry_cache.get(cache_key)
        if cached:
            cache_time = datetime.fromisoformat(cached["checked_at"])
            if (datetime.now(timezone.utc) - cache_time).seconds < 900:  # 15 min cache
                return cached.get("is_excluded", False)

        # Simulated registry response (not excluded)
        self._registry_cache[cache_key] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "is_excluded": False,
        }
        return False

    def _refund_player_balance(self, player_id: str) -> float:
        """
        Refund the player's remaining balance.

        In production, this triggers a withdrawal to their last used
        payment method or holds the funds for collection.
        """
        # Simulated - in production, call wallet service
        simulated_balance = 142.50
        logger.info(f"  Refunding balance: {simulated_balance:.2f} to player {player_id}")
        return simulated_balance

    def _void_pending_bets(self, player_id: str) -> int:
        """
        Void all pending/unsettled bets and return stakes.

        Required by most jurisdictions upon self-exclusion.
        """
        # Simulated - in production, call betting service
        simulated_pending = 3
        logger.info(f"  Voiding {simulated_pending} pending bets for player {player_id}")
        return simulated_pending

    def _remove_from_marketing(self, player_id: str):
        """
        Remove player from ALL marketing lists immediately.

        This includes:
        - Email marketing
        - SMS marketing
        - Push notifications
        - Affiliate tracking
        - Personalized offers
        - Retargeting pixels
        """
        logger.info(f"  Removing player {player_id} from all marketing channels")
        # In production: call CRM API, update marketing preferences,
        # add to suppression lists, notify affiliate system

    def _close_account(self, player_id: str):
        """
        Close the player account immediately.

        The account is suspended, not deleted. Data must be retained
        for regulatory compliance (typically 5-7 years).
        """
        logger.info(f"  Closing account for player {player_id}")
        # In production: update account status to SELF_EXCLUDED,
        # invalidate all sessions, revoke tokens


def run_demo():
    """Run an interactive demonstration of the self-exclusion service."""
    service = SelfExclusionService()

    print("\n" + "=" * 70)
    print("  SELF-EXCLUSION SERVICE DEMO")
    print("=" * 70)

    # Demo 1: Create a self-exclusion
    print("\n--- Demo 1: Player Self-Exclusion (UK/GAMSTOP) ---\n")

    record = service.exclude_player(
        player_id="player-12345",
        duration_months=6,
        jurisdiction="uk",
        player_pii={
            "first_name": "John",
            "last_name": "Smith",
            "date_of_birth": "1990-05-15",
            "postcode": "SW1A 1AA",
            "email": "john.smith@example.com",
        },
        reason="Player requested 6-month self-exclusion via website",
    )

    print(f"\n  Exclusion created: {record.exclusion_id}")
    print(f"  Registry: {record.registry_name} (ref: {record.registry_reference})")
    print(f"  Effective: {record.effective_at}")
    print(f"  End date: {record.end_date}")

    # Demo 2: Check if player is excluded
    print("\n--- Demo 2: Login Check ---\n")

    check = service.check_player_exclusion("player-12345", "uk")
    print(f"  Player excluded: {check.is_excluded}")
    print(f"  Registries checked: {check.registries_checked}")
    if check.exclusion_details:
        print(f"  Source: {check.exclusion_details.get('source')}")

    # Demo 3: Non-excluded player
    print("\n--- Demo 3: Normal Player Login ---\n")

    check2 = service.check_player_exclusion("player-99999", "uk")
    print(f"  Player excluded: {check2.is_excluded}")
    print(f"  Registries checked: {check2.registries_checked}")

    # Demo 4: Reactivation request (will be denied - too early)
    print("\n--- Demo 4: Early Reactivation Attempt ---\n")

    result = service.request_reactivation("player-12345", record.exclusion_id)
    print(f"  Status: {result['status']}")
    print(f"  Reason: {result['reason']}")

    # Demo 5: Exclusion history
    print("\n--- Demo 5: Exclusion History (Compliance Report) ---\n")

    history = service.get_exclusion_history("player-12345")
    print(f"  Total exclusion records: {len(history)}")
    for h in history:
        print(f"  - {h['exclusion_id']}: {h['status']} "
              f"({h['jurisdiction']}, {h['duration_months']}m)")

    # Demo 6: Multiple jurisdiction support
    print("\n--- Demo 6: Swedish Self-Exclusion (Spelpaus) ---\n")

    record2 = service.exclude_player(
        player_id="player-67890",
        duration_months=12,
        jurisdiction="sweden",
        player_pii={"personnummer": "19900515-1234"},
        reason="Player self-exclusion via Spelpaus integration",
    )
    print(f"  Swedish exclusion: {record2.exclusion_id}")
    print(f"  Registry: {record2.registry_name}")

    print("\n" + "=" * 70)
    print("  Available jurisdictions:", ", ".join(JURISDICTION_CONFIG.keys()))
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Self-Exclusion Service Demo")
    parser.add_argument("--demo", action="store_true", help="Run interactive demo")
    parser.add_argument("--list-jurisdictions", action="store_true",
                        help="List supported jurisdictions")

    args = parser.parse_args()

    if args.list_jurisdictions:
        print("\nSupported Jurisdictions:")
        for key, config in JURISDICTION_CONFIG.items():
            print(f"  {key:<12} {config['registry']:<35} "
                  f"Min: {config['min_duration_months']}m  "
                  f"Cooling-off: {config['cooling_off_days']}d")
        print()
        return

    run_demo()


if __name__ == "__main__":
    main()
