#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Bonus Engine with Anti-Abuse Rules
==========================================
Full-featured bonus management system handling bonus creation, claiming,
wagering tracking, and enforcement of anti-abuse rules.

Supported Bonus Types:
- Deposit Match (e.g., 100% up to 100)
- Free Spins (e.g., 50 FS on Starburst, 0.10/spin)
- No Deposit Bonus (e.g., 10 free, 60x wagering)
- Cashback (e.g., 10% of losses, weekly)
- Reload Bonus (e.g., 50% up to 50 on 2nd deposit)
- Tournament Prize (credited as bonus funds)
- Loyalty Points Conversion

Anti-Abuse Rules (industry standard):
- Maximum bet while bonus active (e.g., 5 GBP for slots)
- Game contribution weights (slots 100%, roulette 10%, blackjack 5%)
- Wagering requirements (bonus * multiplier before withdrawal)
- Time limits (bonus expires after N days)
- Minimum deposit for match bonuses
- Maximum win cap from free spins / no-deposit bonuses
- Sticky vs. non-sticky (cashable vs. non-cashable bonus)

Regulatory Notes:
- UK: bonus terms must be "fair and transparent" (ASA/CMA guidance)
- Malta MGA: max 35x wagering recommended
- Sweden: no welcome bonuses since 2020 (Spelinspektionen)
- Ontario AGCO: max 25x wagering, bonus info pre-claim
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class BonusType(Enum):
    DEPOSIT_MATCH = "deposit_match"
    FREE_SPINS = "free_spins"
    NO_DEPOSIT = "no_deposit"
    CASHBACK = "cashback"
    RELOAD = "reload"
    TOURNAMENT_PRIZE = "tournament_prize"
    LOYALTY_CONVERSION = "loyalty_conversion"


class BonusStatus(Enum):
    AVAILABLE = "available"      # Eligible, not yet claimed
    ACTIVE = "active"            # Claimed, wagering in progress
    WAGERING_COMPLETE = "wagering_complete"  # Wagering done, pending release
    RELEASED = "released"        # Converted to real balance
    EXPIRED = "expired"
    FORFEITED = "forfeited"      # Player cancelled or violated terms
    VOIDED = "voided"            # Voided by operator (abuse detected)


class ForfeitReason(Enum):
    PLAYER_CANCELLED = "player_cancelled"
    EXPIRED = "expired"
    MAX_BET_VIOLATION = "max_bet_violation"
    RESTRICTED_GAME = "restricted_game"
    MULTI_ACCOUNTING = "multi_accounting"
    ARBITRAGE_DETECTED = "arbitrage_detected"
    IRREGULAR_PLAY = "irregular_play"
    LOW_RISK_PATTERN = "low_risk_pattern"


# Default game contribution weights (industry standard)
DEFAULT_GAME_WEIGHTS = {
    "slots": 1.00,
    "video_slots": 1.00,
    "scratchcards": 1.00,
    "keno": 1.00,
    "bingo": 1.00,
    "roulette_european": 0.10,
    "roulette_american": 0.10,
    "roulette_french": 0.10,
    "blackjack": 0.05,
    "blackjack_live": 0.05,
    "baccarat": 0.05,
    "baccarat_live": 0.05,
    "video_poker": 0.05,
    "craps": 0.05,
    "sic_bo": 0.10,
    "poker_casino": 0.10,
    "live_game_shows": 0.50,
    "live_roulette": 0.10,
    "live_blackjack": 0.05,
    "sports_betting": 0.00,   # Excluded
    "excluded": 0.00,
}


@dataclass
class BonusTemplate:
    """Template defining a bonus offer.

    NOTE: monetary fields here use ``float`` for illustration only. Production
    money handling uses ``Decimal`` or integer centavos (see ch10 bonus_service,
    ch46 wagering.py) to avoid floating-point drift on real balances.
    """
    template_id: str
    name: str
    bonus_type: BonusType
    match_percentage: float = 100.0     # For deposit match
    max_bonus_amount: float = 100.0     # Cap on bonus credit
    min_deposit: float = 10.0           # Minimum qualifying deposit
    wagering_multiplier: float = 35.0   # Wagering = bonus * multiplier
    wagering_applies_to: str = "bonus"  # "bonus" or "bonus_plus_deposit"
    max_bet_with_bonus: float = 5.0     # Max single bet while bonus active
    max_win_cap: float = 0.0            # 0 = no cap; >0 = cap on winnings
    expiry_days: int = 30
    free_spins_count: int = 0
    free_spins_value: float = 0.10      # Value per spin
    free_spins_game_id: str = ""
    is_sticky: bool = False             # Sticky = bonus removed after wagering
    game_weights: dict = field(default_factory=lambda: DEFAULT_GAME_WEIGHTS.copy())
    restricted_games: list = field(default_factory=list)  # Fully excluded games
    eligible_countries: list = field(default_factory=list)
    excluded_countries: list = field(default_factory=lambda: ["SE", "BE"])  # No bonuses
    max_claims_per_player: int = 1
    requires_promo_code: bool = False
    promo_code: str = ""


@dataclass
class PlayerBonus:
    """An instance of a bonus claimed by a player."""
    bonus_id: str
    player_id: str
    template_id: str
    bonus_type: BonusType
    status: BonusStatus = BonusStatus.AVAILABLE
    bonus_amount: float = 0.0           # Actual bonus credited
    deposit_amount: float = 0.0         # Qualifying deposit
    wagering_requirement: float = 0.0   # Total wagering needed
    wagering_completed: float = 0.0     # Wagering done so far
    wagering_remaining: float = 0.0     # Remaining wagering
    max_bet: float = 5.0
    max_win_cap: float = 0.0
    winnings_from_bonus: float = 0.0    # Running win total
    is_sticky: bool = False
    claimed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    forfeited_at: Optional[datetime] = None
    forfeit_reason: Optional[ForfeitReason] = None
    violations: list = field(default_factory=list)

    @property
    def wagering_progress(self) -> float:
        if self.wagering_requirement <= 0:
            return 1.0
        return min(1.0, self.wagering_completed / self.wagering_requirement)

    @property
    def is_expired(self) -> bool:
        if self.expires_at and datetime.utcnow() > self.expires_at:  # ty:ignore[deprecated]
            return True
        return False


class BonusEngine:
    """
    Core bonus engine managing the full lifecycle of casino bonuses.
    """

    def __init__(self):
        self.templates: dict[str, BonusTemplate] = {}
        self.player_bonuses: dict[str, list[PlayerBonus]] = {}  # player_id -> [bonuses]
        self._bonus_counter = 0

    def register_template(self, template: BonusTemplate):
        """Register a bonus template."""
        self.templates[template.template_id] = template
        logger.info("Registered bonus template: %s (%s)", template.name, template.template_id)

    def claim_bonus(self, player_id: str, template_id: str,
                    deposit_amount: float = 0.0,
                    promo_code: str = "",
                    player_country: str = "GB") -> dict:
        """
        Claim a bonus. Validates eligibility and creates a PlayerBonus instance.

        Returns:
            {"success": bool, "bonus_id": str, "error": str, "bonus": PlayerBonus}
        """
        template = self.templates.get(template_id)
        if not template:
            return {"success": False, "error": "template_not_found"}

        # Eligibility checks
        error = self._check_eligibility(player_id, template, deposit_amount,
                                        promo_code, player_country)
        if error:
            return {"success": False, "error": error}

        # Calculate bonus amount
        bonus_amount = self._calculate_bonus_amount(template, deposit_amount)

        # Calculate wagering requirement
        if template.wagering_applies_to == "bonus_plus_deposit":
            wagering_base = bonus_amount + deposit_amount
        else:
            wagering_base = bonus_amount
        wagering_requirement = wagering_base * template.wagering_multiplier

        # Create bonus instance
        self._bonus_counter += 1
        bonus = PlayerBonus(
            bonus_id=f"bonus_{self._bonus_counter:06d}",
            player_id=player_id,
            template_id=template_id,
            bonus_type=template.bonus_type,
            status=BonusStatus.ACTIVE,
            bonus_amount=bonus_amount,
            deposit_amount=deposit_amount,
            wagering_requirement=round(wagering_requirement, 2),
            wagering_remaining=round(wagering_requirement, 2),
            max_bet=template.max_bet_with_bonus,
            max_win_cap=template.max_win_cap,
            is_sticky=template.is_sticky,
            claimed_at=datetime.utcnow(),  # ty:ignore[deprecated]
            expires_at=datetime.utcnow() + timedelta(days=template.expiry_days),  # ty:ignore[deprecated]
        )

        if player_id not in self.player_bonuses:
            self.player_bonuses[player_id] = []
        self.player_bonuses[player_id].append(bonus)

        logger.info(
            "Bonus claimed: %s by player %s | amount=%.2f | wagering=%.2f",
            bonus.bonus_id, player_id, bonus_amount, wagering_requirement,
        )

        return {"success": True, "bonus_id": bonus.bonus_id, "bonus": bonus}

    def process_bet(self, player_id: str, bet_amount: float,
                    game_type: str, game_id: str) -> dict:
        """
        Process a bet against active bonuses.
        Checks max bet rules, game restrictions, and updates wagering progress.

        Returns:
            {
                "allowed": bool,
                "violation": str or None,
                "wagering_contributed": float,
                "bonus_id": str,
                "wagering_progress": float,
            }
        """
        active_bonus = self._get_active_bonus(player_id)
        if not active_bonus:
            return {"allowed": True, "violation": None, "wagering_contributed": 0}

        # Check expiry
        if active_bonus.is_expired:
            self._forfeit_bonus(active_bonus, ForfeitReason.EXPIRED)
            return {"allowed": True, "violation": None, "wagering_contributed": 0}

        # Check max bet
        if bet_amount > active_bonus.max_bet:
            violation = (
                f"Max bet violation: {bet_amount:.2f} exceeds limit "
                f"{active_bonus.max_bet:.2f}"
            )
            active_bonus.violations.append({
                "type": "max_bet",
                "bet_amount": bet_amount,
                "limit": active_bonus.max_bet,
                "game_id": game_id,
                "timestamp": datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            })
            logger.warning("Max bet violation: player=%s, %s", player_id, violation)

            # Industry practice: first violation = warning, second = forfeit
            if len([v for v in active_bonus.violations if v["type"] == "max_bet"]) >= 2:
                self._forfeit_bonus(active_bonus, ForfeitReason.MAX_BET_VIOLATION)
                return {
                    "allowed": False,
                    "violation": "bonus_forfeited_max_bet",
                    "wagering_contributed": 0,
                    "bonus_id": active_bonus.bonus_id,
                }

            return {
                "allowed": True,  # Allow bet but issue warning
                "violation": violation,
                "wagering_contributed": 0,  # Violation bets don't count
                "bonus_id": active_bonus.bonus_id,
            }

        # Check game restriction
        template = self.templates.get(active_bonus.template_id)
        if template and game_id in template.restricted_games:
            return {
                "allowed": False,
                "violation": f"Game {game_id} is restricted during bonus play",
                "wagering_contributed": 0,
                "bonus_id": active_bonus.bonus_id,
            }

        # Calculate wagering contribution
        weight = self._get_game_weight(game_type, template)
        contribution = bet_amount * weight

        active_bonus.wagering_completed += contribution
        active_bonus.wagering_remaining = max(
            0, active_bonus.wagering_requirement - active_bonus.wagering_completed
        )

        # Check if wagering is complete
        if active_bonus.wagering_remaining <= 0:
            self._complete_wagering(active_bonus)

        return {
            "allowed": True,
            "violation": None,
            "wagering_contributed": round(contribution, 2),
            "bonus_id": active_bonus.bonus_id,
            "wagering_progress": active_bonus.wagering_progress,
            "wagering_remaining": round(active_bonus.wagering_remaining, 2),
        }

    def process_win(self, player_id: str, win_amount: float) -> dict:
        """Track winnings against max win cap."""
        active_bonus = self._get_active_bonus(player_id)
        if not active_bonus:
            return {"capped": False, "win_amount": win_amount}

        active_bonus.winnings_from_bonus += win_amount

        # Apply win cap if set
        if active_bonus.max_win_cap > 0:
            if active_bonus.winnings_from_bonus > active_bonus.max_win_cap:
                excess = active_bonus.winnings_from_bonus - active_bonus.max_win_cap
                active_bonus.winnings_from_bonus = active_bonus.max_win_cap
                return {
                    "capped": True,
                    "win_amount": win_amount - excess,
                    "excess_removed": excess,
                    "total_winnings": active_bonus.winnings_from_bonus,
                }

        return {
            "capped": False,
            "win_amount": win_amount,
            "total_winnings": active_bonus.winnings_from_bonus,
        }

    def forfeit_bonus(self, player_id: str, bonus_id: str) -> bool:
        """Player voluntarily forfeits a bonus."""
        bonuses = self.player_bonuses.get(player_id, [])
        bonus = next((b for b in bonuses if b.bonus_id == bonus_id), None)
        if bonus and bonus.status == BonusStatus.ACTIVE:
            self._forfeit_bonus(bonus, ForfeitReason.PLAYER_CANCELLED)
            return True
        return False

    def get_player_bonuses(self, player_id: str) -> list[dict]:
        """Get all bonuses for a player with current status."""
        bonuses = self.player_bonuses.get(player_id, [])
        return [
            {
                "bonus_id": b.bonus_id,
                "type": b.bonus_type.value,
                "status": b.status.value,
                "amount": b.bonus_amount,
                "wagering_requirement": b.wagering_requirement,
                "wagering_completed": round(b.wagering_completed, 2),
                "wagering_remaining": round(b.wagering_remaining, 2),
                "progress": f"{b.wagering_progress:.1%}",
                "expires_at": b.expires_at.isoformat() if b.expires_at else None,
                "max_bet": b.max_bet,
                "violations": len(b.violations),
            }
            for b in bonuses
        ]

    # --- Internal Methods ---

    def _check_eligibility(self, player_id: str, template: BonusTemplate,
                           deposit_amount: float, promo_code: str,
                           player_country: str) -> Optional[str]:
        """Validate bonus eligibility. Returns error message or None."""
        # Country restrictions
        if template.excluded_countries and player_country in template.excluded_countries:
            return f"Bonuses not available in {player_country}"

        if template.eligible_countries and player_country not in template.eligible_countries:
            return f"Bonus not available in {player_country}"

        # Promo code
        if template.requires_promo_code and promo_code != template.promo_code:
            return "Invalid promo code"

        # Minimum deposit
        if template.bonus_type in (BonusType.DEPOSIT_MATCH, BonusType.RELOAD):
            if deposit_amount < template.min_deposit:
                return f"Minimum deposit is {template.min_deposit}"

        # Max claims
        existing_claims = [
            b for b in self.player_bonuses.get(player_id, [])
            if b.template_id == template.template_id
            and b.status != BonusStatus.VOIDED
        ]
        if len(existing_claims) >= template.max_claims_per_player:
            return "Maximum claims reached for this bonus"

        # No stacking: player must not have another active bonus
        active = self._get_active_bonus(player_id)
        if active:
            return f"Active bonus {active.bonus_id} must be completed or forfeited first"

        return None

    def _calculate_bonus_amount(self, template: BonusTemplate,
                                deposit_amount: float) -> float:
        """Calculate the actual bonus amount to credit."""
        if template.bonus_type == BonusType.DEPOSIT_MATCH:
            raw = deposit_amount * (template.match_percentage / 100)
            return min(raw, template.max_bonus_amount)

        if template.bonus_type == BonusType.FREE_SPINS:
            return template.free_spins_count * template.free_spins_value

        if template.bonus_type == BonusType.NO_DEPOSIT:
            return template.max_bonus_amount

        if template.bonus_type == BonusType.RELOAD:
            raw = deposit_amount * (template.match_percentage / 100)
            return min(raw, template.max_bonus_amount)

        return template.max_bonus_amount

    def _get_active_bonus(self, player_id: str) -> Optional[PlayerBonus]:
        """Get the currently active bonus for a player (if any)."""
        bonuses = self.player_bonuses.get(player_id, [])
        active = [b for b in bonuses if b.status == BonusStatus.ACTIVE]
        return active[0] if active else None

    def _get_game_weight(self, game_type: str,
                         template: Optional[BonusTemplate]) -> float:
        """Get the wagering contribution weight for a game type."""
        if template and game_type in template.game_weights:
            return template.game_weights[game_type]
        return DEFAULT_GAME_WEIGHTS.get(game_type, 0.0)

    def _complete_wagering(self, bonus: PlayerBonus):
        """Mark bonus wagering as complete."""
        bonus.status = BonusStatus.WAGERING_COMPLETE
        bonus.completed_at = datetime.utcnow()  # ty:ignore[deprecated]

        if bonus.is_sticky:
            # Sticky bonus: remove the bonus amount, player keeps winnings
            logger.info(
                "Sticky bonus %s completed. Bonus amount %.2f removed, "
                "winnings %.2f released.",
                bonus.bonus_id, bonus.bonus_amount, bonus.winnings_from_bonus,
            )
        else:
            # Non-sticky: player keeps bonus + winnings
            logger.info(
                "Bonus %s wagering complete. Total: bonus=%.2f + winnings=%.2f",
                bonus.bonus_id, bonus.bonus_amount, bonus.winnings_from_bonus,
            )

        bonus.status = BonusStatus.RELEASED

    def _forfeit_bonus(self, bonus: PlayerBonus, reason: ForfeitReason):
        """Forfeit a bonus due to violation or player request."""
        bonus.status = BonusStatus.FORFEITED
        bonus.forfeited_at = datetime.utcnow()  # ty:ignore[deprecated]
        bonus.forfeit_reason = reason
        logger.info(
            "Bonus %s forfeited: reason=%s, player=%s",
            bonus.bonus_id, reason.value, bonus.player_id,
        )


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = BonusEngine()

    # Register welcome bonus template
    welcome = BonusTemplate(
        template_id="welcome_100",
        name="100% Welcome Bonus up to 100",
        bonus_type=BonusType.DEPOSIT_MATCH,
        match_percentage=100,
        max_bonus_amount=100.0,
        min_deposit=10.0,
        wagering_multiplier=35,
        wagering_applies_to="bonus",
        max_bet_with_bonus=5.0,
        max_win_cap=0,  # No cap on deposit match
        expiry_days=30,
        is_sticky=False,
    )
    engine.register_template(welcome)

    # Register free spins template
    free_spins = BonusTemplate(
        template_id="fs_50_starburst",
        name="50 Free Spins on Starburst",
        bonus_type=BonusType.FREE_SPINS,
        free_spins_count=50,
        free_spins_value=0.10,
        wagering_multiplier=40,
        max_bet_with_bonus=5.0,
        max_win_cap=100.0,  # Max win from free spins
        expiry_days=7,
    )
    engine.register_template(free_spins)

    # Player claims welcome bonus with 50 GBP deposit
    result = engine.claim_bonus("player_1", "welcome_100", deposit_amount=50.0,
                                player_country="GB")
    print(f"Claim result: {result['success']}, bonus={result.get('bonus_id')}")
    if result["success"]:
        bonus = result["bonus"]
        print(f"  Bonus amount: {bonus.bonus_amount}")
        print(f"  Wagering requirement: {bonus.wagering_requirement}")
        print(f"  Expires: {bonus.expires_at}")

    # Simulate slot play (100% contribution)
    for i in range(100):
        bet_result = engine.process_bet("player_1", 2.00, "slots", "starburst")
        if i % 25 == 0:
            print(f"  Bet {i+1}: wagering progress = {bet_result.get('wagering_progress', 0):.1%}")

    # Try a blackjack bet (5% contribution)
    bj_result = engine.process_bet("player_1", 5.00, "blackjack", "bj_classic")
    print(f"  Blackjack bet: contributed {bj_result['wagering_contributed']:.2f} to wagering")

    # Try exceeding max bet
    violation = engine.process_bet("player_1", 10.00, "slots", "starburst")
    print(f"  Max bet violation: {violation.get('violation')}")

    # Check bonus status
    print("\nPlayer bonuses:")
    for b in engine.get_player_bonuses("player_1"):
        print(f"  {b}")

    # Sweden player cannot claim
    se_result = engine.claim_bonus("player_se", "welcome_100", deposit_amount=50.0,
                                   player_country="SE")
    print(f"\nSweden claim: {se_result}")
