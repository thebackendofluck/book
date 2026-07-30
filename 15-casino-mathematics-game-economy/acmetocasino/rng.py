# Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Server-side RNG using Python's secrets module (CSPRNG).

Every RNG call is logged with:
  - timestamp
  - seed hash (SHA-256 of the raw bytes)
  - output value

This provides a complete audit trail for regulatory compliance.
"""

import datetime
import hashlib
import logging
import secrets
from decimal import Decimal

logger = logging.getLogger(__name__)


class ServerRNG:
    """
    Cryptographically Secure Pseudo-Random Number Generator for game outcomes.
    Uses secrets.token_bytes() backed by the OS entropy source.
    """

    @staticmethod
    def _generate_raw(num_bytes: int = 32) -> bytes:
        """Generate raw random bytes from CSPRNG."""
        return secrets.token_bytes(num_bytes)

    @staticmethod
    def _hash_seed(raw: bytes) -> str:
        """SHA-256 hash of raw bytes for audit logging."""
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _bytes_to_float(raw: bytes) -> float:
        """Convert first 8 bytes to a float in [0, 1)."""
        value = int.from_bytes(raw[:8], byteorder="big")
        return value / (2**64)

    def generate_outcome(self, target_rtp: Decimal, bet_amount: Decimal) -> dict:
        """
        Generate a game outcome using CSPRNG.

        The RNG produces a uniform random float in [0, 1).
        The outcome is determined by mapping this float against the
        target RTP to produce a win multiplier.

        Algorithm:
          1. Generate 32 random bytes via secrets.token_bytes()
          2. Convert to float in [0, 1)
          3. Use the float to determine if the round is a win or loss
          4. If win, compute multiplier based on RTP distribution

        Returns:
          {
            "rng_seed_hash": str,
            "rng_output": str,
            "win_amount": Decimal,
            "multiplier": Decimal,
            "timestamp": str,
          }
        """
        raw_bytes = self._generate_raw(32)
        seed_hash = self._hash_seed(raw_bytes)
        roll = self._bytes_to_float(raw_bytes)
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

        rtp_float = float(target_rtp) / 100.0

        # Determine outcome using RTP-weighted distribution
        # ~60% of rounds are losses, remaining ~40% have varying multipliers
        # Adjusted so long-run average payout = target_rtp
        win_amount = Decimal("0.00")
        multiplier = Decimal("0.00")

        # Loss threshold: probability of losing = 1 - (rtp / avg_win_multiplier)
        # Actual avg win multiplier across tiers:
        #   0.50 * avg(1.0,2.0) + 0.30 * avg(2.0,5.0) + 0.15 * avg(5.0,20.0) + 0.05 * avg(20.0,100.0)
        #   = 0.50*1.5 + 0.30*3.5 + 0.15*12.5 + 0.05*60.0 = 6.675
        avg_win_mult = 6.675
        loss_prob = 1.0 - (rtp_float / avg_win_mult)
        loss_prob = max(0.1, min(0.95, loss_prob))  # clamp

        if roll >= loss_prob:
            # Win: distribute multiplier across tiers
            win_roll = self._bytes_to_float(raw_bytes[8:16])

            if win_roll < 0.50:
                # Small win: 1.0x - 2.0x (50% of wins)
                tier_roll = self._bytes_to_float(raw_bytes[16:24])
                multiplier = Decimal(str(round(1.0 + tier_roll, 2)))
            elif win_roll < 0.80:
                # Medium win: 2.0x - 5.0x (30% of wins)
                tier_roll = self._bytes_to_float(raw_bytes[16:24])
                multiplier = Decimal(str(round(2.0 + tier_roll * 3.0, 2)))
            elif win_roll < 0.95:
                # Large win: 5.0x - 20.0x (15% of wins)
                tier_roll = self._bytes_to_float(raw_bytes[16:24])
                multiplier = Decimal(str(round(5.0 + tier_roll * 15.0, 2)))
            else:
                # Jackpot: 20.0x - 100.0x (5% of wins)
                tier_roll = self._bytes_to_float(raw_bytes[16:24])
                multiplier = Decimal(str(round(20.0 + tier_roll * 80.0, 2)))

            win_amount = (bet_amount * multiplier).quantize(Decimal("0.01"))

        result = {
            "rng_seed_hash": seed_hash,
            "rng_output": f"roll={roll:.16f}",
            "win_amount": win_amount,
            "multiplier": multiplier,
            "timestamp": timestamp,
        }

        logger.info(
            "RNG audit | hash=%s | roll=%.16f | rtp_target=%s | bet=%s | win=%s | mult=%s",
            seed_hash[:16],
            roll,
            target_rtp,
            bet_amount,
            win_amount,
            multiplier,
        )

        return result
