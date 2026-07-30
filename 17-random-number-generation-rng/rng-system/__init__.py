# Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Random Number Generation (RNG) System for iGaming Platforms

This module provides enterprise-grade Random Number Generation
implementations specifically designed for online gambling platforms
where RNG quality directly impacts regulatory compliance, player
trust, and financial integrity.

Key Components:
- entropy: Hardware entropy collection and management
- prng: Cryptographically secure pseudo-random number generators
- shuffle: Fisher-Yates shuffle implementation for card games
- testing: NIST SP 800-22 statistical test suite
- game_outcomes: Game-specific RNG adapters (slots, cards, dice)

Regulatory Standards Supported:
- GLI-11: Gaming Laboratories International
- GLI-19: Interactive Gaming Systems
- ISO/IEC 17025: Testing Laboratory Accreditation
- NIST SP 800-22: Statistical Test Suite for Random Number Generators
- NIST SP 800-90A: Recommendation for Random Number Generation Using DRBG

Security Properties:
- Cryptographic unpredictability (AES-256-CTR based)
- Hardware entropy seeding (RDRAND, Zymkey TRNG)
- Forward secrecy (key rotation)
- Audit logging for regulatory compliance
"""

from .entropy import (  # ty:ignore[unresolved-import]
    get_hardware_entropy,
    ZymkeyEntropy,
    EntropyMixer,
    get_timestamp_entropy,
    seed_rng_from_hardware,
)
from .prng import (  # ty:ignore[unresolved-import]
    SecurePRNG,
    AES_CTR_DRBG,
    create_casino_rng,
)
from .shuffle import (  # ty:ignore[unresolved-import]
    fisher_yates_shuffle,
    create_card_deck,
    validate_shuffle,
)
from .game_outcomes import (  # ty:ignore[unresolved-import]
    SlotRNG,
    CardRNG,
    DiceRNG,
    RouletteRNG,
    LotteryRNG,
)
from .testing import (  # ty:ignore[unresolved-import]
    run_nist_tests,
    chi_square_test,
    run_casino_validation,
)

__all__ = [
    # Entropy
    "get_hardware_entropy",
    "ZymkeyEntropy",
    "EntropyMixer",
    "get_timestamp_entropy",
    "seed_rng_from_hardware",
    # PRNG
    "SecurePRNG",
    "AES_CTR_DRBG",
    "create_casino_rng",
    # Shuffle
    "fisher_yates_shuffle",
    "create_card_deck",
    "validate_shuffle",
    # Game Outcomes
    "SlotRNG",
    "CardRNG",
    "DiceRNG",
    "RouletteRNG",
    "LotteryRNG",
    # Testing
    "run_nist_tests",
    "chi_square_test",
    "run_casino_validation",
]

__version__ = "1.0.0"
