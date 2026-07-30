#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
YubiHSM 2 Password Vault Manager
Secure password management system using YubiHSM 2 FIPS hardware security module
"""

import sys
import os
import json
import secrets
import string
import argparse
import getpass
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

try:
    from yubihsm import YubiHsm
    from yubihsm.defs import CAPABILITY, ALGORITHM, ERROR, OBJECT
    # The class is `Opaque`, not `OpaqueObject`. python-yubihsm has never
    # exported an `OpaqueObject` name (checked 2.0.0 through current), so the
    # old import raised ImportError, was caught below, and the script exited 1
    # before doing anything at all.
    from yubihsm.exceptions import YubiHsmDeviceError
    from yubihsm.objects import AsymmetricKey, Opaque, WrapKey
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.backends import default_backend
except ImportError as e:
    print("Error: Required module not found. Please install: pip install yubihsm[http,usb] cryptography")
    print(f"Missing module: {e}")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration constants
HSM_CONNECTOR_URL = os.getenv('YUBIHSM_CONNECTOR_URL', 'http://localhost:12345')
DEFAULT_AUTH_KEY_ID = 2
DEFAULT_WRAP_KEY_ID = 3
VAULT_KEY_ID = 10
PASSWORD_DOMAIN = 1
AUDIT_DOMAIN = 2

# Software-fallback envelope encryption parameters (used when VAULT_ENCRYPTION_KEY
# is derived outside the HSM). Salt is per-entry and random; iteration count follows
# the OWASP 2024 PBKDF2-HMAC-SHA256 floor.
VAULT_KDF_SALT_LEN = 16
VAULT_KDF_ITERATIONS = 600_000
VAULT_AESGCM_NONCE_LEN = 12

# Entries are addressed by label, not by an ID derived from the name.
#
# The old scheme was `int(sha256(name)[:8], 16) % 65000`, which is a 65,000-slot
# hash with no collision handling. By the birthday bound two names collide with
# probability ~50% at around 300 entries, and the consequence was not an error:
# retrieve_password() computed the colliding ID, fetched the *other* entry, and
# decrypted it successfully, because the ciphertext is self-contained and the
# name is inside it rather than being checked against it. The caller asked for
# one credential and got a different one, fully decrypted, with no indication
# anything had gone wrong.
#
# Instead: the YubiHSM allocates the object ID itself (object_id=0), and lookup
# goes through a label carrying a 128-bit digest of the name. Labels are limited
# to 40 bytes; "pwd:" plus 32 hex characters is 36. Collisions are no longer
# improbable, they are infeasible.
VAULT_LABEL_PREFIX = "pwd:"
VAULT_LABEL_DIGEST_CHARS = 32

class VaultError(Exception):
    """Base class for vault errors."""

class VaultEntryNotFound(VaultError):
    """No entry exists for the requested name."""

class VaultIntegrityError(VaultError):
    """An entry exists but failed authentication.

    Kept distinct from VaultEntryNotFound on purpose. Both used to surface as
    "not found", which meant a tampered or truncated entry -- the single most
    security-relevant event this vault can observe -- was indistinguishable from
    a typo in the entry name. One is routine, the other means either the HSM
    contents were modified or VAULT_ENCRYPTION_KEY has changed, and both of
    those need a human rather than a retry.
    """

class PasswordCategory(Enum):
    """Password categories for organization"""
    DATABASE = "database"
    SERVER = "server"
    APPLICATION = "application"
    SERVICE = "service"
    DISK = "disk"
    CERTIFICATE = "certificate"
    API = "api"
    OTHER = "other"

@dataclass
class PasswordEntry:
    """Password entry data structure"""
    name: str
    username: str
    password: str
    category: PasswordCategory
    description: str
    created_at: datetime
    modified_at: datetime
    last_rotation: Optional[datetime] = None
    rotation_interval_days: int = 90
    metadata: Optional[Dict] = None

    def to_json(self) -> str:
        """Convert to JSON for storage"""
        data = asdict(self)
        data['category'] = self.category.value
        data['created_at'] = self.created_at.isoformat()
        data['modified_at'] = self.modified_at.isoformat()
        if self.last_rotation:
            data['last_rotation'] = self.last_rotation.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, json_str: str) -> 'PasswordEntry':
        """Create from JSON string"""
        data = json.loads(json_str)
        data['category'] = PasswordCategory(data['category'])
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['modified_at'] = datetime.fromisoformat(data['modified_at'])
        if data.get('last_rotation'):
            data['last_rotation'] = datetime.fromisoformat(data['last_rotation'])
        return cls(**data)

class YubiHSMPasswordVault:
    """Main password vault manager using YubiHSM 2"""
    
    def __init__(self, connector_url: str = HSM_CONNECTOR_URL):
        self.connector_url = connector_url
        self.hsm = None
        self.session = None

    @property
    def sess(self):
        """The authenticated session, or a clear error if there isn't one.

        Every vault operation went straight at `self.session`, which starts as
        None, so calling anything before connect() produced
        "'NoneType' object has no attribute 'list_objects'" from somewhere deep
        in the call stack.
        """
        if self.session is None:
            raise VaultError("no YubiHSM session — call connect() first")
        return self.session

    def connect(self, auth_key_id: int = DEFAULT_AUTH_KEY_ID, password: Optional[str] = None):
        """Connect to YubiHSM 2 and establish authenticated session"""
        try:
            logger.info(f"Connecting to YubiHSM at {self.connector_url}")
            self.hsm = YubiHsm.connect(self.connector_url)
            
            if password is None:
                password = getpass.getpass(f"Enter password for auth key {auth_key_id}: ")
            
            self.session = self.hsm.create_session_derived(auth_key_id, password)
            logger.info("Successfully connected to YubiHSM")
            
            # Verify FIPS mode
            if hasattr(self.session, 'get_fips_mode'):
                fips_mode = self.session.get_fips_mode()
                if fips_mode:
                    logger.info("YubiHSM is running in FIPS mode")
                else:
                    logger.warning("YubiHSM is NOT in FIPS mode. Enable FIPS mode for compliance.")
                    
        except Exception as e:
            logger.error(f"Failed to connect to YubiHSM: {e}")
            raise
    
    def disconnect(self):
        """Close YubiHSM session"""
        if self.session:
            self.session.close()
            logger.info("Disconnected from YubiHSM")
    
    def initialize_vault(self):
        """Initialize password vault with required keys"""
        try:
            logger.info("Initializing password vault...")
            
            # Create wrap key for password encryption.
            # delegated_capabilities is a required argument: it is the set of
            # capabilities objects wrapped by this key are allowed to carry.
            # Omitting it raised TypeError, so vault initialisation never
            # completed. EXPORTABLE_UNDER_WRAP belongs here, on the delegated
            # set, and not in the wrap key's own capabilities -- a wrap key that
            # is itself exportable under wrap can be carried off the device.
            wrap_key = WrapKey.generate(
                session=self.sess,
                object_id=DEFAULT_WRAP_KEY_ID,
                label="Password Vault Wrap Key",
                domains=PASSWORD_DOMAIN,
                capabilities=CAPABILITY.EXPORT_WRAPPED | CAPABILITY.IMPORT_WRAPPED |
                            CAPABILITY.WRAP_DATA | CAPABILITY.UNWRAP_DATA,
                algorithm=ALGORITHM.AES256_CCM_WRAP,
                delegated_capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP |
                                       CAPABILITY.GET_OPAQUE |
                                       CAPABILITY.DELETE_OPAQUE,
            )
            logger.info(f"Created wrap key ID: {wrap_key.id}")
            
            # Create audit key for logging
            audit_key = AsymmetricKey.generate(
                session=self.sess,
                object_id=0,  # Auto-assign ID
                label="Vault Audit Key",
                domains=AUDIT_DOMAIN,
                capabilities=CAPABILITY.SIGN_ECDSA | CAPABILITY.VERIFY_ECDSA,
                algorithm=ALGORITHM.EC_P256
            )
            logger.info(f"Created audit key ID: {audit_key.id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize vault: {e}")
            return False
    
    def _hsm_random(self, count: int) -> bytes:
        """Fetch random bytes from the HSM TRNG."""
        return self.sess.get_pseudo_random(count)

    def generate_password(self, length: int = 24, include_special: bool = True) -> str:
        """Generate a secure random password.

        Uses rejection sampling over the character set. Reducing a uniform byte
        with `b % len(characters)` is not uniform unless len(characters) divides
        256: with the full 94-character set, 256 % 94 == 68, so the first 68
        characters of the alphabet are drawn from 3 byte values each while the
        remaining 26 are drawn from 2. That is roughly 1.5x the weight on the
        first two thirds of the alphabet and it costs about half a bit of entropy
        per character. Note the fallback path in this same function already did
        it correctly via secrets.choice, so the HSM path -- the one used in
        production -- was the weaker of the two.
        """
        characters = string.ascii_letters + string.digits
        if include_special:
            characters += string.punctuation
        n = len(characters)

        # Largest multiple of n that fits in a byte. Bytes at or above this are
        # discarded rather than folded, which keeps every character equally
        # likely. For n=94 the limit is 188, so ~73% of bytes are accepted.
        limit = 256 - (256 % n)

        chars: List[str] = []
        try:
            while len(chars) < length:
                # Over-fetch so the loop is not one HSM round trip per accepted
                # byte. The HSM call is the only statement that can raise here.
                block = self._hsm_random(max(length * 2, 32))
                chars.extend(characters[b % n] for b in block if b < limit)
        except Exception as e:
            logger.warning(
                f"HSM random number generation failed, falling back to system random: {e}"
            )
            return ''.join(secrets.choice(characters) for _ in range(length))

        return ''.join(chars[:length])

    @staticmethod
    def _entry_label(name: str) -> str:
        """Collision-free label for an entry name.

        A 128-bit truncation of SHA-256, which fits the YubiHSM's 40-byte label
        field. The name itself is not in the label: names can exceed the field
        and the old truncation to 20 characters made two long names with a shared
        prefix indistinguishable.
        """
        digest = hashlib.sha256(name.encode()).hexdigest()[:VAULT_LABEL_DIGEST_CHARS]
        return f"{VAULT_LABEL_PREFIX}{digest}"

    def _find_entry_object(self, name: str) -> Optional[Opaque]:
        """Return the Opaque object holding `name`, or None.

        The label filter is applied by the YubiHSM itself, so this is one round
        trip and an exact match.
        """
        matches = self.sess.list_objects(
            object_type=OBJECT.OPAQUE,
            label=self._entry_label(name),
        )
        if not matches:
            return None
        if len(matches) > 1:
            # Should be unreachable: object IDs are unique and the label is a
            # 128-bit digest. Surfaced rather than silently picking one, because
            # if it ever happens the vault's addressing assumption is broken.
            raise VaultError(
                f"{len(matches)} objects share the label for '{name}' "
                f"(ids: {[m.id for m in matches]}) — refusing to guess"
            )
        return Opaque(self.sess, matches[0].id)

    def store_password(self, entry: PasswordEntry, replace: bool = False) -> int:
        """Store password entry in YubiHSM.

        `replace=True` is required to overwrite an existing entry. The YubiHSM
        has no update-in-place for opaque objects: PUT_OPAQUE against an
        existing ID returns OBJECT_EXISTS. That is what silently broke rotation
        -- update_password() called this method on an entry that already
        existed, the device rejected the put, the exception was swallowed by
        update_password's `except`, it returned False, and rotate_passwords
        reported zero rotations forever while believing it had tried.
        """
        encrypted_data = self._encrypt_password_entry(entry)
        existing = self._find_entry_object(entry.name)

        if existing is not None and not replace:
            raise VaultError(
                f"entry '{entry.name}' already exists (id {existing.id}); "
                "use update_password() or pass replace=True"
            )

        previous_data: Optional[bytes] = None
        if existing is not None:
            # Read the old ciphertext before deleting it, so a failed put can be
            # rolled back. Delete-then-put has a window where the entry does not
            # exist; without the rollback, a rotation that fails halfway loses
            # the credential outright.
            try:
                previous_data = existing.get()
            except YubiHsmDeviceError as e:
                logger.warning(f"could not read existing entry before replace: {e}")
            existing.delete()

        try:
            opaque = Opaque.put(
                session=self.sess,
                object_id=0,  # let the YubiHSM allocate a free ID
                label=self._entry_label(entry.name),
                domains=PASSWORD_DOMAIN,
                # Opaque objects are read with GET_OPAQUE and removed with
                # DELETE_OPAQUE. CAPABILITY has no OPAQUE_READ / OPAQUE_WRITE
                # members; naming them raised AttributeError.
                capabilities=CAPABILITY.GET_OPAQUE | CAPABILITY.DELETE_OPAQUE,
                # OPAQUE_DATA, not AES256: those are the only algorithms an
                # Opaque object accepts (OPAQUE_DATA or OPAQUE_X509_CERTIFICATE).
                algorithm=ALGORITHM.OPAQUE_DATA,
                data=encrypted_data,
            )
        except Exception:
            if previous_data is not None:
                logger.error(
                    f"put failed while replacing '{entry.name}' — restoring previous entry"
                )
                Opaque.put(
                    session=self.sess,
                    object_id=0,
                    label=self._entry_label(entry.name),
                    domains=PASSWORD_DOMAIN,
                    capabilities=CAPABILITY.GET_OPAQUE | CAPABILITY.DELETE_OPAQUE,
                    algorithm=ALGORITHM.OPAQUE_DATA,
                    data=previous_data,
                )
            raise

        logger.info(f"Stored password for '{entry.name}' with ID: {opaque.id}")
        self._audit_log("STORE" if previous_data is None else "REPLACE",
                        entry.name, entry.username)
        return opaque.id

    def retrieve_password(self, name: str) -> Optional[PasswordEntry]:
        """Retrieve password entry from YubiHSM.

        Returns None when the entry does not exist. Raises VaultIntegrityError
        when it exists but does not authenticate -- see that class for why the
        two are not collapsed into one.
        """
        try:
            obj = self._find_entry_object(name)
        except YubiHsmDeviceError as e:
            logger.error(f"HSM error looking up '{name}': {e}")
            raise

        if obj is None:
            logger.info(f"No password entry named '{name}'")
            return None

        try:
            encrypted_data = obj.get()
        except YubiHsmDeviceError as e:
            if e.code == ERROR.OBJECT_NOT_FOUND:
                logger.info(f"No password entry named '{name}'")
                return None
            raise

        entry = self._decrypt_password_entry(encrypted_data)

        # The name is inside the authenticated ciphertext, so this check is
        # meaningful: it confirms the object we found by label really is the
        # entry that was asked for.
        if entry.name != name:
            raise VaultIntegrityError(
                f"entry at id {obj.id} decrypts to name '{entry.name}', "
                f"expected '{name}' — vault addressing is inconsistent"
            )

        logger.info(f"Retrieved password for '{name}'")
        self._audit_log("RETRIEVE", name, entry.username)
        return entry

    def update_password(self, name: str, new_password: Optional[str] = None) -> bool:
        """Update existing password entry"""
        try:
            entry = self.retrieve_password(name)
            if entry is None:
                logger.error(f"Password entry '{name}' not found")
                return False

            if new_password is None:
                new_password = self.generate_password()

            entry.password = new_password
            entry.modified_at = datetime.now()
            entry.last_rotation = datetime.now()

            # replace=True: the entry exists by definition, we just read it.
            self.store_password(entry, replace=True)

            logger.info(f"Updated password for '{name}'")
            self._audit_log("UPDATE", name, entry.username)

            return True

        except VaultIntegrityError:
            # Never rewrite an entry that failed authentication: doing so would
            # overwrite the evidence of tampering with a fresh valid entry.
            logger.error(f"Refusing to rotate '{name}': integrity check failed")
            raise
        except Exception as e:
            logger.error(f"Failed to update password for '{name}': {e}")
            return False

    def delete_password(self, name: str) -> bool:
        """Delete password entry from YubiHSM"""
        try:
            obj = self._find_entry_object(name)
            if obj is None:
                logger.error(f"Password entry '{name}' not found")
                return False

            obj.delete()

            logger.info(f"Deleted password for '{name}'")
            self._audit_log("DELETE", name, "")

            return True

        except Exception as e:
            logger.error(f"Failed to delete password: {e}")
            return False

    def _vault_objects(self) -> List[Tuple[Opaque, str]]:
        """Return (object, label) for every vault entry in the password domain.

        list_objects returns YhsmObject references that carry an id but not a
        label -- reading `obj.label` raised AttributeError, which was caught by
        the caller's blanket `except` and turned into an empty list. That is the
        second reason rotate_passwords reported zero rotations: it iterated over
        nothing. The label needs an explicit get_info() call.
        """
        found: List[Tuple[Opaque, str]] = []
        for obj in self.sess.list_objects(object_type=OBJECT.OPAQUE,
                                             domains=PASSWORD_DOMAIN):
            try:
                label = obj.get_info().label
            except YubiHsmDeviceError as e:
                logger.warning(f"could not read info for object {obj.id}: {e}")
                continue
            if label.startswith(VAULT_LABEL_PREFIX):
                found.append((Opaque(self.sess, obj.id), label))
        return found

    def list_passwords(self, category: Optional[PasswordCategory] = None) -> List[Dict]:
        """List all password entries.

        Entries that fail authentication are reported with
        `integrity_failed: True` rather than skipped. A silently skipped entry
        looks the same as an entry that was never there, which is how a
        tampered vault would pass an inventory check.
        """
        passwords: List[Dict] = []
        integrity_failures = 0

        for obj, label in self._vault_objects():
            try:
                entry = self._decrypt_password_entry(obj.get())
            except (InvalidTag, VaultIntegrityError) as e:
                integrity_failures += 1
                logger.error(
                    f"INTEGRITY FAILURE on object {obj.id} (label {label}): {e}. "
                    "The stored data does not authenticate. Either it was modified "
                    "in the HSM or VAULT_ENCRYPTION_KEY has changed."
                )
                passwords.append({
                    'name': f"<unreadable id={obj.id}>",
                    'username': '',
                    'category': '',
                    'description': 'INTEGRITY CHECK FAILED',
                    'created_at': '',
                    'modified_at': '',
                    'needs_rotation': False,
                    'integrity_failed': True,
                })
                continue
            except Exception as e:
                logger.error(f"could not read object {obj.id}: {e}")
                continue

            if category is None or entry.category == category:
                passwords.append({
                    'name': entry.name,
                    'username': entry.username,
                    'category': entry.category.value,
                    'description': entry.description,
                    'created_at': entry.created_at.isoformat(),
                    'modified_at': entry.modified_at.isoformat(),
                    'needs_rotation': self._needs_rotation(entry),
                    'integrity_failed': False,
                })

        if integrity_failures:
            logger.error(
                f"{integrity_failures} vault entr"
                f"{'y' if integrity_failures == 1 else 'ies'} failed integrity checks"
            )

        return passwords

    def rotate_passwords(self, force: bool = False) -> List[str]:
        """Rotate passwords that need rotation.

        Returns the names actually rotated. Entries that fail their integrity
        check are never rotated and are counted separately: rewriting them would
        destroy the evidence.
        """
        rotated: List[str] = []
        failed: List[str] = []

        for pwd_info in self.list_passwords():
            if pwd_info.get('integrity_failed'):
                failed.append(pwd_info['name'])
                continue
            if not (force or pwd_info['needs_rotation']):
                continue
            try:
                if self.update_password(pwd_info['name']):
                    rotated.append(pwd_info['name'])
                    logger.info(f"Rotated password for '{pwd_info['name']}'")
                else:
                    failed.append(pwd_info['name'])
            except VaultError as e:
                failed.append(pwd_info['name'])
                logger.error(f"Rotation failed for '{pwd_info['name']}': {e}")

        logger.info(f"Rotated {len(rotated)} passwords")
        if failed:
            logger.error(f"{len(failed)} entries could not be rotated: {', '.join(failed)}")
        return rotated

    def export_passwords(self, output_file: str, wrap_key_id: int = DEFAULT_WRAP_KEY_ID) -> bool:
        """Export all passwords wrapped with specified key"""
        try:
            wrap_key = WrapKey(self.sess, wrap_key_id)

            exports = []
            for obj, label in self._vault_objects():
                # export_wrapped is a method on the WrapKey, taking the object to
                # export. The reverse (obj.export_wrapped(wrap_key)) is not part
                # of the API -- YhsmObject has no such method.
                wrapped = wrap_key.export_wrapped(obj)
                exports.append({
                    'id': obj.id,
                    'label': label,
                    'wrapped_data': wrapped.hex()
                })

            # The export is ciphertext under the wrap key, but it is still every
            # credential in the vault in one file. 0600 from the moment it is
            # created, not after.
            fd = os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(exports, f, indent=2)

            logger.info(f"Exported {len(exports)} passwords to {output_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to export passwords: {e}")
            return False


    def _derive_vault_key(self, salt: bytes) -> bytes:
        """Derive the software-fallback envelope key from VAULT_ENCRYPTION_KEY.

        NOTE: In production, replace with HSM-backed key derivation.
        The VAULT_ENCRYPTION_KEY must be set; a predictable fallback would be
        insecure. Uses a random per-entry salt (not a fixed/absent one) so
        identical secrets don't derive identical keys and offline dictionary
        attacks can't be precomputed once.
        """
        key_env = os.environ.get('VAULT_ENCRYPTION_KEY')
        if not key_env:
            raise RuntimeError(
                "VAULT_ENCRYPTION_KEY environment variable is required for encryption"
            )
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=VAULT_KDF_ITERATIONS,
            backend=default_backend()
        )
        return kdf.derive(key_env.encode())

    def _encrypt_password_entry(self, entry: PasswordEntry) -> bytes:
        """Encrypt password entry data with AES-256-GCM (authenticated)."""
        # Serialize entry to JSON
        json_data = entry.to_json().encode()

        salt = os.urandom(VAULT_KDF_SALT_LEN)
        key = self._derive_vault_key(salt)

        nonce = os.urandom(VAULT_AESGCM_NONCE_LEN)
        ciphertext = AESGCM(key).encrypt(nonce, json_data, None)

        # salt + nonce + ciphertext (GCM tag is appended to ciphertext)
        return salt + nonce + ciphertext

    def _decrypt_password_entry(self, encrypted_data: bytes) -> PasswordEntry:
        """Decrypt and authenticate password entry data (AES-256-GCM).

        Raises VaultIntegrityError on anything that means the stored bytes are
        not an authentic entry. Callers must not translate that into "missing".
        """
        minimum = VAULT_KDF_SALT_LEN + VAULT_AESGCM_NONCE_LEN + 16  # + GCM tag
        if len(encrypted_data) < minimum:
            raise VaultIntegrityError(
                f"stored blob is {len(encrypted_data)} bytes, too short to be a "
                f"valid entry (minimum {minimum}) — truncated or not vault data"
            )

        salt = encrypted_data[:VAULT_KDF_SALT_LEN]
        nonce = encrypted_data[VAULT_KDF_SALT_LEN:VAULT_KDF_SALT_LEN + VAULT_AESGCM_NONCE_LEN]
        ciphertext = encrypted_data[VAULT_KDF_SALT_LEN + VAULT_AESGCM_NONCE_LEN:]

        key = self._derive_vault_key(salt)

        try:
            json_data = AESGCM(key).decrypt(nonce, ciphertext, None)
        except InvalidTag as e:
            raise VaultIntegrityError(
                "AES-GCM authentication failed: the stored entry was modified, or "
                "VAULT_ENCRYPTION_KEY does not match the one used to store it"
            ) from e

        try:
            return PasswordEntry.from_json(json_data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            # Authenticated but unparseable means the schema changed under us,
            # not that an attacker got in. Still not "missing".
            raise VaultIntegrityError(
                f"entry authenticated but could not be parsed: {e}"
            ) from e
    
    def _needs_rotation(self, entry: PasswordEntry) -> bool:
        """Check if password needs rotation"""
        if entry.last_rotation is None:
            last_change = entry.created_at
        else:
            last_change = entry.last_rotation
        
        days_since_change = (datetime.now() - last_change).days
        return days_since_change >= entry.rotation_interval_days
    
    def _audit_log(self, action: str, resource: str, username: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        message = f"{timestamp} | {action} | {resource} | {username}"
        logger.info(f"AUDIT: {message}")
        # In production, also log to YubiHSM audit log

def main():
    """Main CLI interface"""
    parser = argparse.ArgumentParser(description='YubiHSM 2 Password Vault Manager')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Init command
    parser_init = subparsers.add_parser('init', help='Initialize password vault')
    
    # Store command
    parser_store = subparsers.add_parser('store', help='Store a password')
    parser_store.add_argument('--name', required=True, help='Password entry name')
    parser_store.add_argument('--username', required=True, help='Username')
    parser_store.add_argument('--category', default='other', 
                            choices=[c.value for c in PasswordCategory],
                            help='Password category')
    parser_store.add_argument('--description', default='', help='Description')
    parser_store.add_argument('--password', help='Password (will prompt if not provided). Avoid passing via CLI as it may appear in shell history; prefer the interactive prompt.')
    parser_store.add_argument('--generate', action='store_true', help='Generate random password')
    
    # Get command
    parser_get = subparsers.add_parser('get', help='Retrieve a password')
    parser_get.add_argument('--name', required=True, help='Password entry name')
    parser_get.add_argument('--show', action='store_true', help='Show password in output')
    
    # Update command
    parser_update = subparsers.add_parser('update', help='Update a password')
    parser_update.add_argument('--name', required=True, help='Password entry name')
    parser_update.add_argument('--password', help='New password (will generate if not provided)')
    
    # Delete command
    parser_delete = subparsers.add_parser('delete', help='Delete a password')
    parser_delete.add_argument('--name', required=True, help='Password entry name')
    
    # List command
    parser_list = subparsers.add_parser('list', help='List passwords')
    parser_list.add_argument('--category', choices=[c.value for c in PasswordCategory],
                           help='Filter by category')
    
    # Rotate command
    parser_rotate = subparsers.add_parser('rotate', help='Rotate passwords')
    parser_rotate.add_argument('--force', action='store_true', help='Force rotation of all passwords')
    
    # Export command
    parser_export = subparsers.add_parser('export', help='Export passwords')
    parser_export.add_argument('--output', required=True, help='Output file path')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize vault
    vault = YubiHSMPasswordVault()
    
    try:
        # Connect to YubiHSM
        vault.connect()
        
        # Execute command
        if args.command == 'init':
            if vault.initialize_vault():
                print("Password vault initialized successfully")
            else:
                print("Failed to initialize password vault")
                
        elif args.command == 'store':
            # Get or generate password
            if args.generate:
                password = vault.generate_password()
                print(f"Generated password: {password}")
            elif args.password:
                password = args.password
            else:
                password = getpass.getpass("Enter password: ")
            
            # Create entry
            entry = PasswordEntry(
                name=args.name,
                username=args.username,
                password=password,
                category=PasswordCategory(args.category),
                description=args.description,
                created_at=datetime.now(),
                modified_at=datetime.now()
            )
            
            # Store entry
            entry_id = vault.store_password(entry)
            print(f"Stored password for '{args.name}' with ID: {entry_id}")
            
        elif args.command == 'get':
            try:
                entry = vault.retrieve_password(args.name)
            except VaultIntegrityError as e:
                # Exit 2, distinct from "not found", so a monitoring wrapper can
                # page on tampering without paging on typos.
                print(f"INTEGRITY FAILURE for '{args.name}': {e}")
                print("This entry exists but does not authenticate. Do not treat it as absent.")
                sys.exit(2)
            if entry:
                print(f"Name: {entry.name}")
                print(f"Username: {entry.username}")
                print(f"Category: {entry.category.value}")
                print(f"Description: {entry.description}")
                print(f"Created: {entry.created_at}")
                print(f"Modified: {entry.modified_at}")
                if args.show:
                    print(f"Password: {entry.password}")
                else:
                    print("Password: [hidden - use --show to display]")
            else:
                print(f"Password entry '{args.name}' not found")
                
        elif args.command == 'update':
            if vault.update_password(args.name, args.password):
                print(f"Password updated for '{args.name}'")
            else:
                print(f"Failed to update password for '{args.name}'")
                
        elif args.command == 'delete':
            if vault.delete_password(args.name):
                print(f"Password deleted for '{args.name}'")
            else:
                print(f"Failed to delete password for '{args.name}'")
                
        elif args.command == 'list':
            category = PasswordCategory(args.category) if args.category else None
            passwords = vault.list_passwords(category)
            
            if passwords:
                print(f"Found {len(passwords)} password(s):")
                broken = 0
                for pwd in passwords:
                    if pwd.get('integrity_failed'):
                        broken += 1
                        print(f"  ! {pwd['name']} - INTEGRITY CHECK FAILED")
                        continue
                    rotation = " [NEEDS ROTATION]" if pwd['needs_rotation'] else ""
                    print(f"  - {pwd['name']} ({pwd['username']}) - {pwd['category']}{rotation}")
                if broken:
                    print(f"\n{broken} entr{'y' if broken == 1 else 'ies'} failed "
                          "integrity checks and are shown with '!' above.")
                    print("These are NOT missing entries. Investigate before rotating anything.")
                    sys.exit(2)
            else:
                print("No passwords found")
                
        elif args.command == 'rotate':
            rotated = vault.rotate_passwords(force=args.force)
            if rotated:
                print(f"Rotated {len(rotated)} password(s):")
                for name in rotated:
                    print(f"  - {name}")
            else:
                print("No passwords needed rotation")
                
        elif args.command == 'export':
            if vault.export_passwords(args.output):
                print(f"Passwords exported to {args.output}")
            else:
                print("Failed to export passwords")
        
    except Exception as e:
        logger.error(f"Command failed: {e}")
        sys.exit(1)
        
    finally:
        vault.disconnect()

if __name__ == '__main__':
    main()