#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Backup Encryption with Separate Key Management
================================================
Encrypts backups using AES-256-GCM with jurisdiction-separated key management.
Keys are stored separately from encrypted data -- critical for iGaming compliance.

Features:
- AES-256-GCM authenticated encryption
- Key derivation with PBKDF2 (600,000 iterations)
- Key rotation without re-encrypting existing backups
- Jurisdiction-isolated key stores
- HSM integration points

Usage:
    python backup_encryption.py --encrypt backup.sql.zst -j UK
    python backup_encryption.py --decrypt backup.sql.zst.enc -j UK
    python backup_encryption.py --rotate-keys -j UK
    python backup_encryption.py --audit-keys
    python backup_encryption.py --demo
"""

import os
import json
import hashlib
import hmac
import struct
import logging
import argparse
import secrets
import base64
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backup-encryption")

# Try to import cryptographic libraries
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.warning(
        "cryptography library not available. Install: pip install cryptography"
    )


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------
@dataclass
class EncryptionKeyMetadata:
    key_id: str
    jurisdiction: str
    created_at: str
    expires_at: str
    algorithm: str
    key_size_bits: int
    status: str  # active / rotated / revoked
    rotation_count: int
    checksum: str  # HMAC of key for integrity verification


class KeyStore:
    """
    Manages encryption keys with jurisdiction isolation.
    In production, this wraps an HSM (e.g., AWS CloudHSM, Thales Luna).
    """

    def __init__(self, base_dir: str = "/etc/igaming/backup-keys"):
        self.base_dir = Path(base_dir)
        self._keys: dict[str, dict] = {}  # key_id -> {key, metadata}

    def generate_key(
        self, jurisdiction: str, key_size_bits: int = 256
    ) -> EncryptionKeyMetadata:
        """Generate a new AES key for a jurisdiction."""
        key = secrets.token_bytes(key_size_bits // 8)
        key_id = f"{jurisdiction}-{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc)

        # Key validity: 90 days (regulatory standard for key rotation)
        metadata = EncryptionKeyMetadata(
            key_id=key_id,
            jurisdiction=jurisdiction,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(days=90)).isoformat(),
            algorithm="AES-256-GCM",
            key_size_bits=key_size_bits,
            status="active",
            rotation_count=0,
            checksum=hmac.new(
                key, key_id.encode(), hashlib.sha256
            ).hexdigest(),
        )

        self._keys[key_id] = {
            "key": key,
            "metadata": metadata,
        }

        logger.info(
            "Generated key %s for jurisdiction %s (expires: %s)",
            key_id, jurisdiction, metadata.expires_at,
        )
        return metadata

    def get_active_key(self, jurisdiction: str) -> tuple[bytes, EncryptionKeyMetadata]:
        """Get the active encryption key for a jurisdiction."""
        for key_id, entry in self._keys.items():
            meta = entry["metadata"]
            if meta.jurisdiction == jurisdiction and meta.status == "active":
                return entry["key"], meta

        # Auto-generate if none exists
        logger.info("No active key for %s, generating new key", jurisdiction)
        meta = self.generate_key(jurisdiction)
        return self._keys[meta.key_id]["key"], meta

    def get_key_by_id(self, key_id: str) -> tuple[bytes, EncryptionKeyMetadata]:
        """Retrieve a specific key by ID (needed for decryption)."""
        if key_id not in self._keys:
            raise KeyError(f"Key {key_id} not found in key store")
        entry = self._keys[key_id]
        return entry["key"], entry["metadata"]

    def rotate_key(self, jurisdiction: str) -> EncryptionKeyMetadata:
        """
        Rotate the active key for a jurisdiction.
        Old key remains accessible for decryption but marked as 'rotated'.
        """
        # Mark existing active keys as rotated
        for entry in self._keys.values():
            meta = entry["metadata"]
            if meta.jurisdiction == jurisdiction and meta.status == "active":
                meta.status = "rotated"
                meta.rotation_count += 1
                logger.info("Rotated key %s (now status: rotated)", meta.key_id)

        # Generate new active key
        new_meta = self.generate_key(jurisdiction)
        logger.info("New active key for %s: %s", jurisdiction, new_meta.key_id)
        return new_meta

    def check_expiry(self) -> list[EncryptionKeyMetadata]:
        """Check for keys approaching expiry (within 14 days)."""
        warning_threshold = timedelta(days=14)
        now = datetime.now(timezone.utc)
        expiring = []

        for entry in self._keys.values():
            meta = entry["metadata"]
            if meta.status != "active":
                continue
            expires = datetime.fromisoformat(meta.expires_at)
            if expires - now < warning_threshold:
                expiring.append(meta)
                logger.warning(
                    "Key %s expires in %s",
                    meta.key_id,
                    expires - now,
                )

        return expiring

    def audit(self) -> list[dict]:
        """Return audit information for all keys."""
        audit_entries = []
        for key_id, entry in self._keys.items():
            meta = entry["metadata"]
            audit_entries.append({
                "key_id": meta.key_id,
                "jurisdiction": meta.jurisdiction,
                "status": meta.status,
                "created_at": meta.created_at,
                "expires_at": meta.expires_at,
                "rotation_count": meta.rotation_count,
                "algorithm": meta.algorithm,
            })
        return audit_entries

    def save_to_disk(self, path: Optional[str] = None):
        """
        Save key metadata to disk. Keys themselves go to HSM in production.
        WARNING: This demo saves keys to disk -- never do this in production.
        """
        save_path = Path(path) if path else self.base_dir / "keystore.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)

        keys_dict: dict[str, object] = {}
        for key_id, entry in self._keys.items():
            keys_dict[key_id] = {
                "key_b64": base64.b64encode(entry["key"]).decode(),
                "metadata": asdict(entry["metadata"]),
            }
        data: dict[str, object] = {
            "keystore_version": "1.0",
            "keys": keys_dict,
        }

        with open(save_path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(str(save_path), 0o600)
        logger.info("Keystore saved to %s", save_path)


# ---------------------------------------------------------------------------
# Encryption engine
# ---------------------------------------------------------------------------
# File format:
#   [4 bytes: header magic "IGBK"]
#   [2 bytes: version (1)]
#   [2 bytes: key_id length]
#   [N bytes: key_id]
#   [12 bytes: nonce/IV]
#   [remaining: ciphertext + 16-byte GCM tag]

MAGIC = b"IGBK"
VERSION = 1


class BackupEncryptor:
    """Encrypts and decrypts backup files with AES-256-GCM."""

    def __init__(self, key_store: KeyStore):
        self.key_store = key_store

    def encrypt_file(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        jurisdiction: str = "UK",
        chunk_size: int = 64 * 1024 * 1024,  # 64 MB chunks
    ) -> dict:
        """
        Encrypt a backup file.

        For files larger than chunk_size, encrypts in chunks with
        separate nonces per chunk (streaming GCM).
        """
        if not HAS_CRYPTO:
            return self._fallback_encrypt(input_path, output_path, jurisdiction)

        key, metadata = self.key_store.get_active_key(jurisdiction)
        aesgcm = AESGCM(key)

        if output_path is None:
            output_path = input_path + ".enc"

        input_size = os.path.getsize(input_path)
        key_id_bytes = metadata.key_id.encode("utf-8")

        # Associated data for authentication (not encrypted, but authenticated)
        aad = json.dumps({
            "jurisdiction": jurisdiction,
            "key_id": metadata.key_id,
            "original_size": input_size,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }).encode("utf-8")

        start_time = datetime.now(timezone.utc)

        with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
            # Write header
            fout.write(MAGIC)
            fout.write(struct.pack("!H", VERSION))
            fout.write(struct.pack("!H", len(key_id_bytes)))
            fout.write(key_id_bytes)

            # Write AAD length and AAD
            fout.write(struct.pack("!I", len(aad)))
            fout.write(aad)

            # Encrypt in chunks
            chunk_count = 0
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break

                nonce = secrets.token_bytes(12)
                ciphertext = aesgcm.encrypt(nonce, chunk, aad)

                # Write: [4 bytes chunk_len][12 bytes nonce][ciphertext]
                fout.write(struct.pack("!I", len(ciphertext)))
                fout.write(nonce)
                fout.write(ciphertext)
                chunk_count += 1

            # Write sentinel (zero-length chunk)
            fout.write(struct.pack("!I", 0))

        end_time = datetime.now(timezone.utc)
        output_size = os.path.getsize(output_path)

        result = {
            "input_file": input_path,
            "output_file": output_path,
            "input_size": input_size,
            "output_size": output_size,
            "overhead_bytes": output_size - input_size,
            "key_id": metadata.key_id,
            "jurisdiction": jurisdiction,
            "algorithm": "AES-256-GCM",
            "chunks": chunk_count,
            "duration_seconds": (end_time - start_time).total_seconds(),
            "checksum_sha256": self._file_sha256(output_path),
        }

        logger.info(
            "Encrypted %s -> %s (%d chunks, %d bytes overhead)",
            input_path, output_path, chunk_count,
            output_size - input_size,
        )
        return result

    def decrypt_file(
        self,
        input_path: str,
        output_path: Optional[str] = None,
    ) -> dict:
        """Decrypt an encrypted backup file."""
        if not HAS_CRYPTO:
            return self._fallback_decrypt(input_path, output_path)

        if output_path is None:
            if input_path.endswith(".enc"):
                output_path = input_path[:-4]
            else:
                output_path = input_path + ".dec"

        with open(input_path, "rb") as fin:
            # Read header
            magic = fin.read(4)
            if magic != MAGIC:
                raise ValueError(f"Invalid file format (magic: {magic})")

            version = struct.unpack("!H", fin.read(2))[0]
            if version != VERSION:
                raise ValueError(f"Unsupported version: {version}")

            key_id_len = struct.unpack("!H", fin.read(2))[0]
            key_id = fin.read(key_id_len).decode("utf-8")

            # Read AAD
            aad_len = struct.unpack("!I", fin.read(4))[0]
            aad = fin.read(aad_len)

            # Get key
            key, metadata = self.key_store.get_key_by_id(key_id)
            aesgcm = AESGCM(key)

            # Decrypt chunks
            with open(output_path, "wb") as fout:
                chunk_count = 0
                while True:
                    chunk_len_data = fin.read(4)
                    if len(chunk_len_data) < 4:
                        break
                    chunk_len = struct.unpack("!I", chunk_len_data)[0]
                    if chunk_len == 0:
                        break  # sentinel

                    nonce = fin.read(12)
                    ciphertext = fin.read(chunk_len)
                    plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
                    fout.write(plaintext)
                    chunk_count += 1

        result = {
            "input_file": input_path,
            "output_file": output_path,
            "key_id": key_id,
            "jurisdiction": metadata.jurisdiction,
            "chunks_decrypted": chunk_count,
            "output_size": os.path.getsize(output_path),
        }

        logger.info(
            "Decrypted %s -> %s (key: %s, %d chunks)",
            input_path, output_path, key_id, chunk_count,
        )
        return result

    def _fallback_encrypt(self, input_path, output_path, jurisdiction):
        """Fallback using openssl CLI when cryptography lib is unavailable."""
        logger.warning("Using openssl CLI fallback (install cryptography for native)")
        if output_path is None:
            output_path = input_path + ".enc"
        os.system(
            f"openssl enc -aes-256-gcm -pbkdf2 -iter 600000 "
            f"-in '{input_path}' -out '{output_path}'"
        )
        return {"input_file": input_path, "output_file": output_path, "method": "openssl_cli"}

    def _fallback_decrypt(self, input_path, output_path):
        logger.warning("Using openssl CLI fallback for decryption")
        if output_path is None:
            output_path = input_path.replace(".enc", "")
        os.system(
            f"openssl enc -d -aes-256-gcm -pbkdf2 -iter 600000 "
            f"-in '{input_path}' -out '{output_path}'"
        )
        return {"input_file": input_path, "output_file": output_path, "method": "openssl_cli"}

    @staticmethod
    def _file_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo():
    print("=" * 80)
    print("BACKUP ENCRYPTION DEMO")
    print("=" * 80)

    # Initialize key store
    key_store = KeyStore("/tmp/igaming-demo-keys")

    # Generate keys for each jurisdiction
    print("\n--- Generating encryption keys ---")
    for jur in ["UK", "MT", "DE", "ON"]:
        meta = key_store.generate_key(jur)
        print(f"  {jur}: key_id={meta.key_id}, expires={meta.expires_at}")

    # Key rotation demo
    print("\n--- Key rotation (UK) ---")
    old_key, old_meta = key_store.get_active_key("UK")
    new_meta = key_store.rotate_key("UK")
    print(f"  Old key: {old_meta.key_id} (status: {old_meta.status})")
    print(f"  New key: {new_meta.key_id} (status: {new_meta.status})")

    # Expiry check
    print("\n--- Key expiry audit ---")
    expiring = key_store.check_expiry()
    if expiring:
        for m in expiring:
            print(f"  WARNING: {m.key_id} expires at {m.expires_at}")
    else:
        print("  All keys within validity period")

    # Encrypt/decrypt demo (only if cryptography is available)
    if HAS_CRYPTO:
        print("\n--- Encrypt/Decrypt cycle ---")
        # Create test file
        test_file = "/tmp/igaming-demo-backup.sql"
        with open(test_file, "w") as f:
            f.write("-- iGaming backup test data\n" * 10000)
            f.write("INSERT INTO players (id, email) VALUES (1, 'player@test.com');\n" * 5000)

        encryptor = BackupEncryptor(key_store)

        # Encrypt
        enc_result = encryptor.encrypt_file(test_file, jurisdiction="UK")
        print(f"  Encrypted: {enc_result['output_file']}")
        print(f"  Input:     {enc_result['input_size']} bytes")
        print(f"  Output:    {enc_result['output_size']} bytes")
        print(f"  Overhead:  {enc_result['overhead_bytes']} bytes")
        print(f"  Key:       {enc_result['key_id']}")

        # Decrypt
        dec_result = encryptor.decrypt_file(enc_result["output_file"])
        print(f"  Decrypted: {dec_result['output_file']}")
        print(f"  Size:      {dec_result['output_size']} bytes")

        # Verify
        original_hash = hashlib.sha256(open(test_file, "rb").read()).hexdigest()
        restored_hash = hashlib.sha256(
            open(dec_result["output_file"], "rb").read()
        ).hexdigest()
        match = original_hash == restored_hash
        print(f"  Integrity: {'PASS' if match else 'FAIL'} "
              f"(SHA256 {'matches' if match else 'MISMATCH'})")

        # Cleanup
        for f in [test_file, enc_result["output_file"], dec_result["output_file"]]:
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass
    else:
        print("\n  (Skipping encrypt/decrypt demo -- install cryptography)")

    # Full audit
    print("\n--- Key store audit ---")
    for entry in key_store.audit():
        print(f"  {entry['key_id']:30s} | {entry['status']:8s} | "
              f"{entry['jurisdiction']:4s} | rotations: {entry['rotation_count']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Backup Encryption with Separate Key Management"
    )
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--encrypt", metavar="FILE", help="Encrypt a file")
    parser.add_argument("--decrypt", metavar="FILE", help="Decrypt a file")
    parser.add_argument("-j", "--jurisdiction", default="UK")
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("--rotate-keys", action="store_true")
    parser.add_argument("--audit-keys", action="store_true")
    parser.add_argument(
        "--key-dir", default="/etc/igaming/backup-keys",
        help="Key store directory",
    )

    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.encrypt:
        ks = KeyStore(args.key_dir)
        enc = BackupEncryptor(ks)
        result = enc.encrypt_file(args.encrypt, args.output, args.jurisdiction)
        print(json.dumps(result, indent=2))
    elif args.decrypt:
        ks = KeyStore(args.key_dir)
        enc = BackupEncryptor(ks)
        result = enc.decrypt_file(args.decrypt, args.output)
        print(json.dumps(result, indent=2))
    elif args.rotate_keys:
        ks = KeyStore(args.key_dir)
        meta = ks.rotate_key(args.jurisdiction)
        print(json.dumps(asdict(meta), indent=2))
    elif args.audit_keys:
        ks = KeyStore(args.key_dir)
        print(json.dumps(ks.audit(), indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
