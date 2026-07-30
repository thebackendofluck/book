# Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 4: Online Poker Platform Architecture
Security Implementation

This module contains the security-related classes for the poker platform:
- SecurityManager: Anti-cheating, bot detection, and collusion detection
- EncryptionService: AES/RSA encryption for sensitive data
- CertifiedRNG: Cryptographically certified random number generation
- RNGAuditor: Audit trail for shuffle operations

Reference: Chapter 4 - Security Architecture and Random Number Generation sections
"""

import base64
import hashlib
import os
import secrets
import time

from Crypto.Cipher import AES
from Crypto.PublicKey import RSA


class SecurityManager:
    def __init__(self):
        self.pattern_detector = PatternDetector()  # ty:ignore[unresolved-reference]
        self.collusion_detector = CollusionDetector()  # ty:ignore[unresolved-reference]
        self.bot_detector = BotDetector()  # ty:ignore[unresolved-reference]

    async def analyze_player_behavior(self, player_id):
        """Detect suspicious patterns"""
        metrics = {
            'reaction_times': self.get_reaction_times(player_id),  # ty:ignore[possibly-missing-attribute]
            'betting_patterns': self.get_betting_patterns(player_id),  # ty:ignore[possibly-missing-attribute]
            'win_rate': self.calculate_win_rate(player_id),  # ty:ignore[possibly-missing-attribute]
            'session_duration': self.get_session_duration(player_id)  # ty:ignore[possibly-missing-attribute]
        }

        # Check for bot-like behavior
        if self.bot_detector.is_suspicious(metrics):
            await self.flag_for_review(player_id, 'BOT_SUSPECTED')  # ty:ignore[possibly-missing-attribute]

        # Check for collusion
        table_players = self.get_table_players(player_id)  # ty:ignore[possibly-missing-attribute]
        if self.collusion_detector.detect(table_players):
            await self.flag_for_review(table_players, 'COLLUSION_SUSPECTED')  # ty:ignore[possibly-missing-attribute]


class EncryptionService:
    def __init__(self):
        self.rsa_key = RSA.generate(2048)
        self.aes_key = secrets.token_bytes(32)

    def encrypt_sensitive_data(self, data):
        """Encrypt player cards and sensitive info"""
        cipher = AES.new(self.aes_key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(data.encode())
        return {
            'ciphertext': base64.b64encode(ciphertext).decode(),
            'tag': base64.b64encode(tag).decode(),
            'nonce': base64.b64encode(cipher.nonce).decode()
        }

    def secure_card_dealing(self):
        """Mental poker protocol for secure dealing"""
        # Each player contributes to shuffling
        # Cards are encrypted with multiple keys
        # Revelation requires cooperation
        pass


class CertifiedRNG:
    """
    Implements a certified Random Number Generator
    for fair card dealing
    """
    def __init__(self):
        self.hardware_rng = HardwareRNG()  # Hardware RNG device  # ty:ignore[unresolved-reference]
        self.seed_pool = []
        self.reseed_counter = 0

    def generate_seed(self):
        """Generate cryptographically secure seed"""
        # Combine multiple entropy sources
        entropy = (
            self.hardware_rng.get_random_bytes(32) +
            os.urandom(32) +
            self.get_timing_entropy()  # ty:ignore[possibly-missing-attribute]
        )
        return hashlib.sha256(entropy).digest()

    def shuffle_deck(self, deck):
        """Fisher-Yates shuffle with certified RNG"""
        n = len(deck)
        for i in range(n-1, 0, -1):
            j = self.get_random_int(0, i)
            deck[i], deck[j] = deck[j], deck[i]
        return deck

    def get_random_int(self, min_val, max_val):
        """Generate random integer in range"""
        range_size = max_val - min_val + 1
        num_bytes = (range_size.bit_length() + 7) // 8

        while True:
            random_bytes = self.hardware_rng.get_random_bytes(num_bytes)
            random_int = int.from_bytes(random_bytes, 'big')
            if random_int < range_size:
                return min_val + random_int


class RNGAuditor:
    def __init__(self):
        self.audit_log = []

    def log_shuffle(self, deck_id, seed, result_hash):
        """Log shuffle operation for audit"""
        entry = {
            'timestamp': time.time(),
            'deck_id': deck_id,
            'seed_hash': hashlib.sha256(seed).hexdigest(),
            'result_hash': result_hash,
            'signature': self.generate_signature(deck_id, seed, result_hash)  # ty:ignore[possibly-missing-attribute]
        }
        self.audit_log.append(entry)
        self.persist_to_database(entry)  # ty:ignore[possibly-missing-attribute]

    def verify_shuffle(self, deck_id):
        """Verify shuffle was fair and untampered"""
        audit_entry = self.get_audit_entry(deck_id)  # ty:ignore[possibly-missing-attribute]
        return self.verify_signature(audit_entry)  # ty:ignore[possibly-missing-attribute]
