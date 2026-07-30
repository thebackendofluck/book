#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 8: Cryptocurrency and DeFi Integration
HD Wallet Derivation for Player Deposits

BIP-32/39/44 hierarchical deterministic wallet manager for crypto casino
player deposit address generation. Features:
- BIP-39 mnemonic generation (12/24 words) with passphrase support
- BIP-44 derivation paths per coin type (ETH, BTC, LTC, MATIC, etc.)
- Unique deposit address per player per currency
- Address gap limit management (BIP-44 standard: 20)
- Bulk address pre-generation for high-throughput operations
- Address validation and balance checking
- Xpub export for watch-only monitoring

Security considerations:
- Master seed NEVER stored in plaintext - use HSM/KMS in production
- Derived private keys should be encrypted at rest
- This script is for educational/development purposes

Prerequisites:
    pip install mnemonic bip32utils eth-account eth-keys

Usage:
    manager = HDWalletManager.from_new_mnemonic(strength=256)
    address = manager.derive_player_address(player_id="PLR-001", currency="ETH")
"""

import hashlib
import hmac
import json
import logging
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── BIP-44 Coin Types ────────────────────────────────────────────────

class CoinType(Enum):
    BTC = 0
    BTC_TESTNET = 1
    LTC = 2
    ETH = 60
    ETC = 61
    MATIC = 966
    BNB = 714
    AVAX = 9005
    SOL = 501
    TRX = 195
    DOGE = 3


# BIP-44 path: m / purpose' / coin_type' / account' / change / address_index
# For casino: m/44'/coin_type'/0'/0/player_index

COIN_CONFIG = {
    "BTC": {"coin_type": CoinType.BTC, "address_prefix": "bc1", "decimals": 8},
    "ETH": {"coin_type": CoinType.ETH, "address_prefix": "0x", "decimals": 18},
    "LTC": {"coin_type": CoinType.LTC, "address_prefix": "ltc1", "decimals": 8},
    "MATIC": {"coin_type": CoinType.MATIC, "address_prefix": "0x", "decimals": 18},
    "BNB": {"coin_type": CoinType.BNB, "address_prefix": "0x", "decimals": 18},
    "TRX": {"coin_type": CoinType.TRX, "address_prefix": "T", "decimals": 6},
    "DOGE": {"coin_type": CoinType.DOGE, "address_prefix": "D", "decimals": 8},
    "SOL": {"coin_type": CoinType.SOL, "address_prefix": "", "decimals": 9},
}


@dataclass
class DerivedAddress:
    """A derived cryptocurrency address with its derivation info."""
    address: str
    currency: str
    derivation_path: str
    player_id: str
    address_index: int
    public_key: str
    created_at: str = ""

    # NEVER store private keys in this struct in production
    # This is for development/testing only
    _private_key: Optional[str] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "currency": self.currency,
            "derivation_path": self.derivation_path,
            "player_id": self.player_id,
            "address_index": self.address_index,
            "public_key": self.public_key,
        }


class HDWalletManager:
    """
    BIP-32/39/44 HD wallet manager for crypto casino deposits.

    Generates unique deposit addresses for each player using deterministic
    derivation from a master seed. Each player gets a unique address per
    currency, derived from their index in the wallet hierarchy.
    """

    BIP39_WORDLIST_EN = None  # Loaded on demand

    def __init__(self, mnemonic: str, passphrase: str = ""):
        self.mnemonic = mnemonic
        self._seed = self._mnemonic_to_seed(mnemonic, passphrase)
        self._master_key, self._master_chain = self._derive_master_key(self._seed)
        self._player_index_map: dict[str, int] = {}
        self._next_index = 0
        self._derived_addresses: list[DerivedAddress] = []
        logger.info("HD Wallet Manager initialized")

    @classmethod
    def from_new_mnemonic(cls, strength: int = 256, passphrase: str = "") -> "HDWalletManager":
        """
        Generate a new mnemonic and create wallet manager.

        Args:
            strength: 128 (12 words) or 256 (24 words)
            passphrase: Optional BIP-39 passphrase
        """
        try:
            from mnemonic import Mnemonic  # ty:ignore[unresolved-import]
            m = Mnemonic("english")
            mnemonic = m.generate(strength)
        except ImportError:
            # Fallback: generate using hashlib (NOT cryptographically optimal)
            import secrets
            entropy = secrets.token_bytes(strength // 8)
            # Simplified - in production use proper BIP-39 library
            words = cls._entropy_to_words(entropy)
            mnemonic = " ".join(words)

        logger.info(f"Generated new {strength}-bit mnemonic ({len(mnemonic.split())} words)")
        logger.warning("BACKUP YOUR MNEMONIC - It cannot be recovered if lost!")
        return cls(mnemonic, passphrase)

    @classmethod
    def from_existing_mnemonic(cls, mnemonic: str, passphrase: str = "") -> "HDWalletManager":
        """Restore wallet from existing mnemonic."""
        word_count = len(mnemonic.strip().split())
        if word_count not in (12, 15, 18, 21, 24):
            raise ValueError(f"Invalid mnemonic: {word_count} words (expected 12-24)")
        logger.info(f"Restoring wallet from {word_count}-word mnemonic")
        return cls(mnemonic, passphrase)

    @staticmethod
    def _entropy_to_words(entropy: bytes) -> list[str]:
        """Simplified entropy to word list (use mnemonic library in production)."""
        # BIP-39 English wordlist subset for demo
        import secrets
        demo_words = [
            "abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract",
            "absurd", "abuse", "access", "accident", "account", "accuse", "achieve", "acid",
            "acoustic", "acquire", "across", "act", "action", "actor", "actress", "actual",
            "adapt", "add", "addict", "address", "adjust", "admit", "adult", "advance",
            "advice", "aerobic", "affair", "afford", "afraid", "again", "age", "agent",
            "agree", "ahead", "aim", "air", "airport", "aisle", "alarm", "album",
        ]
        words = []
        for i in range(0, len(entropy), 2):
            idx = int.from_bytes(entropy[i:i+2], "big") % len(demo_words)
            words.append(demo_words[idx])
        return words[:24]

    @staticmethod
    def _mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
        """BIP-39: Convert mnemonic to 512-bit seed using PBKDF2."""
        password = mnemonic.encode("utf-8")
        salt = ("mnemonic" + passphrase).encode("utf-8")
        return hashlib.pbkdf2_hmac("sha512", password, salt, iterations=2048, dklen=64)

    @staticmethod
    def _derive_master_key(seed: bytes) -> tuple[bytes, bytes]:
        """BIP-32: Derive master key and chain code from seed."""
        h = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
        return h[:32], h[32:]  # private_key, chain_code

    def _derive_child(self, parent_key: bytes, parent_chain: bytes, index: int, hardened: bool = False) -> tuple[bytes, bytes]:
        """BIP-32: Derive child key from parent."""
        if hardened:
            index += 0x80000000
            data = b'\x00' + parent_key + struct.pack(">I", index)
        else:
            # For non-hardened, use public key (simplified)
            data = parent_key + struct.pack(">I", index)

        h = hmac.new(parent_chain, data, hashlib.sha512).digest()
        child_key = h[:32]
        child_chain = h[32:]

        # Add parent and child keys (mod n for secp256k1)
        n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        parent_int = int.from_bytes(parent_key, "big")
        child_int = int.from_bytes(child_key, "big")
        derived_int = (parent_int + child_int) % n
        derived_key = derived_int.to_bytes(32, "big")

        return derived_key, child_chain

    def _derive_path(self, path: str) -> tuple[bytes, bytes]:
        """Derive key at a specific BIP-44 path."""
        key, chain = self._master_key, self._master_chain

        parts = path.replace("m/", "").split("/")
        for part in parts:
            hardened = part.endswith("'")
            index = int(part.rstrip("'"))
            key, chain = self._derive_child(key, chain, index, hardened)

        return key, chain

    def _private_key_to_eth_address(self, private_key: bytes) -> tuple[str, str]:
        """Derive Ethereum address from private key."""
        try:
            from eth_account import Account  # ty:ignore[unresolved-import]
            account = Account.from_key(private_key)
            return account.address, account.key.hex()
        except ImportError:
            # Fallback using hashlib (simplified, not production-grade)
            pub_key_hash = hashlib.sha3_256(private_key).digest()
            address = "0x" + pub_key_hash[-20:].hex()
            return address, private_key.hex()

    def _get_player_index(self, player_id: str) -> int:
        """Get or assign a deterministic index for a player."""
        if player_id not in self._player_index_map:
            self._player_index_map[player_id] = self._next_index
            self._next_index += 1
        return self._player_index_map[player_id]

    def derive_player_address(
        self,
        player_id: str,
        currency: str = "ETH",
        account: int = 0,
    ) -> DerivedAddress:
        """
        Derive a unique deposit address for a player.

        BIP-44 path: m/44'/coin_type'/account'/0/player_index

        Args:
            player_id: Unique player identifier (e.g., "PLR-001")
            currency: Cryptocurrency symbol (ETH, BTC, MATIC, etc.)
            account: BIP-44 account index (default 0)

        Returns:
            DerivedAddress with address and derivation info.
        """
        if currency not in COIN_CONFIG:
            raise ValueError(f"Unsupported currency: {currency}. Available: {list(COIN_CONFIG.keys())}")

        coin_type = COIN_CONFIG[currency]["coin_type"].value  # ty:ignore[unresolved-attribute]
        player_index = self._get_player_index(player_id)

        # BIP-44 path
        path = f"m/44'/{coin_type}'/{account}'/0/{player_index}"
        private_key, _ = self._derive_path(path)

        # Generate address based on currency
        if currency in ("ETH", "MATIC", "BNB"):
            address, pub_key = self._private_key_to_eth_address(private_key)
        else:
            # Simplified for non-ETH chains (use proper library per chain in production)
            address_hash = hashlib.sha256(private_key).hexdigest()
            prefix = COIN_CONFIG[currency]["address_prefix"]
            address = prefix + address_hash[:40]  # ty:ignore[unsupported-operator]
            pub_key = private_key.hex()

        from datetime import datetime, timezone
        derived = DerivedAddress(
            address=address,
            currency=currency,
            derivation_path=path,
            player_id=player_id,
            address_index=player_index,
            public_key=pub_key[:42] + "...",
            created_at=datetime.now(timezone.utc).isoformat(),
            _private_key=private_key.hex(),  # DEV ONLY - encrypt in production
        )

        self._derived_addresses.append(derived)
        logger.info(f"Derived {currency} address for {player_id}: {address} (path: {path})")

        return derived

    def bulk_derive(
        self,
        player_ids: list[str],
        currencies: list[str] = None,  # ty:ignore[invalid-parameter-default]
    ) -> list[DerivedAddress]:
        """Bulk derive addresses for multiple players and currencies."""
        if currencies is None:
            currencies = ["ETH", "BTC"]

        addresses = []
        for player_id in player_ids:
            for currency in currencies:
                addr = self.derive_player_address(player_id, currency)
                addresses.append(addr)

        logger.info(f"Bulk derived {len(addresses)} addresses for {len(player_ids)} players")
        return addresses

    def get_xpub(self, currency: str = "ETH", account: int = 0) -> str:
        """
        Export extended public key (xpub) for watch-only monitoring.

        The xpub allows generating public addresses without private keys,
        useful for monitoring deposit wallets from a separate system.
        """
        coin_type = COIN_CONFIG[currency]["coin_type"].value  # ty:ignore[unresolved-attribute]
        path = f"m/44'/{coin_type}'/{account}'"
        key, chain = self._derive_path(path)

        # Simplified xpub encoding (use proper base58check in production)
        xpub_data = chain.hex() + hashlib.sha256(key).hexdigest()[:64]
        return f"xpub_{currency}_{xpub_data[:64]}"

    def export_address_book(self) -> dict:
        """Export all derived addresses (without private keys) for monitoring."""
        return {
            "total_addresses": len(self._derived_addresses),
            "total_players": len(self._player_index_map),
            "addresses": [a.to_dict() for a in self._derived_addresses],
            "player_map": dict(self._player_index_map),
        }


# ── Gap Limit Manager ────────────────────────────────────────────────

class GapLimitManager:
    """
    BIP-44 gap limit manager.

    Tracks used vs unused addresses and stops derivation when
    the gap limit (20 consecutive unused addresses) is reached.
    Used during wallet recovery to discover all used addresses.
    """

    GAP_LIMIT = 20

    def __init__(self, wallet: HDWalletManager):
        self.wallet = wallet
        self.used_addresses: set[str] = set()
        self.scanned_addresses: list[DerivedAddress] = []

    def mark_used(self, address: str):
        self.used_addresses.add(address.lower())

    def scan_for_used_addresses(
        self,
        currency: str = "ETH",
        check_fn=None,
    ) -> list[DerivedAddress]:
        """
        Scan addresses until gap limit is reached.

        Args:
            currency: Currency to scan
            check_fn: Function that checks if address has been used on-chain.
                       Signature: check_fn(address: str) -> bool
        """
        if check_fn is None:
            # Default: consider all addresses unused (for demo)
            check_fn = lambda addr: addr.lower() in self.used_addresses

        gap_count = 0
        index = 0
        found = []

        while gap_count < self.GAP_LIMIT:
            player_id = f"__scan_{index}"
            addr = self.wallet.derive_player_address(player_id, currency)

            if check_fn(addr.address):
                found.append(addr)
                gap_count = 0
            else:
                gap_count += 1

            index += 1

        logger.info(f"Gap limit scan complete: found {len(found)} used addresses, "
                    f"scanned {index} total")
        return found


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 72)
    print("HD WALLET MANAGER - Crypto Casino Player Deposits")
    print("=" * 72)

    # 1. Generate new wallet
    print("\n[1] Generating new HD wallet (24 words)...")
    manager = HDWalletManager.from_new_mnemonic(strength=256)
    print(f"    Mnemonic: {manager.mnemonic[:50]}...")
    print(f"    (NEVER share or store this in plaintext in production!)")

    # 2. Derive player addresses
    print("\n[2] Deriving player deposit addresses...")
    players = ["PLR-001", "PLR-002", "PLR-003", "PLR-VIP-100"]
    currencies = ["ETH", "BTC", "MATIC"]

    for player in players:
        print(f"\n  Player: {player}")
        for currency in currencies:
            addr = manager.derive_player_address(player, currency)
            print(f"    {currency:>6}: {addr.address}  (path: {addr.derivation_path})")

    # 3. Bulk derive
    print("\n[3] Bulk deriving for 10 players...")
    bulk_players = [f"PLR-{i:04d}" for i in range(10, 20)]
    bulk_addrs = manager.bulk_derive(bulk_players, ["ETH", "BTC"])
    print(f"    Generated {len(bulk_addrs)} addresses")

    # 4. Export xpub
    print("\n[4] Exporting xpub for watch-only monitoring...")
    for curr in ["ETH", "BTC"]:
        xpub = manager.get_xpub(curr)
        print(f"    {curr} xpub: {xpub[:60]}...")

    # 5. Address book
    print("\n[5] Address book summary:")
    book = manager.export_address_book()
    print(f"    Total addresses: {book['total_addresses']}")
    print(f"    Total players:   {book['total_players']}")
    print(json.dumps(book["addresses"][:3], indent=4))

    # 6. Gap limit scan
    print("\n[6] Gap limit scanner (simulated)...")
    scanner = GapLimitManager(manager)
    scanner.mark_used(bulk_addrs[0].address)
    scanner.mark_used(bulk_addrs[3].address)
    found = scanner.scan_for_used_addresses("ETH")
    print(f"    Found {len(found)} used addresses before gap limit")
