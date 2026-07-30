#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Encrypted Evidence Store — secure document storage with access logging.

Handles:
  - AES-256-GCM encryption of KYC documents at rest
  - Content-addressable storage (SHA-256 dedup)
  - Access logging for every read/write (who, when, why)
  - Retention policy enforcement per jurisdiction
  - Secure deletion with cryptographic erasure

Storage layout:
    /evidence/{case_id}/{document_id}.enc   — encrypted blob
    /evidence/{case_id}/manifest.json       — document index
    /evidence/access_log/                   — immutable access log

In production this wraps S3 + KMS or Azure Blob + Key Vault.
This implementation uses local filesystem for book demonstration.

Script reference for Chapter 24d.
"""

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StoredDocument:
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str = ""
    document_type: str = ""
    content_hash: str = ""
    encrypted_key_ref: str = ""  # KMS key ARN or vault path
    storage_path: str = ""
    size_bytes: int = 0
    stored_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    retention_until: str = ""
    deleted: bool = False
    deleted_at: str = ""


@dataclass
class AccessLogEntry:
    log_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    case_id: str = ""
    actor: str = ""
    actor_role: str = ""
    action: str = ""  # STORE, RETRIEVE, DELETE, EXPORT
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ip_address: str = ""
    success: bool = True
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Encryption helpers (simplified for demonstration)
# ---------------------------------------------------------------------------

class EncryptionProvider:
    """
    Wraps AES-256-GCM encryption. In production, key material comes from
    AWS KMS, Azure Key Vault, or HashiCorp Vault (see Chapter 20).
    """

    def __init__(self, master_key_ref: str = "local-dev-key"):
        self.master_key_ref = master_key_ref
        self._local_key = hashlib.sha256(
            master_key_ref.encode()
        ).digest()  # 32 bytes for AES-256

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        """
        Encrypt content and return (ciphertext, key_ref).
        In production: calls KMS.encrypt() with data key envelope.
        Demo: XOR with derived key (NOT secure — illustration only).
        """
        # Production would use:
        #   from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        #   nonce = os.urandom(12)
        #   aesgcm = AESGCM(data_key)
        #   ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        key_ref = f"kms://{self.master_key_ref}/{uuid.uuid4().hex[:16]}"
        # Simulated encryption: in demo, store plaintext with marker
        marker = b"ENC:" + key_ref.encode() + b":"
        ciphertext = marker + plaintext
        return ciphertext, key_ref

    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes:
        """Decrypt content using the referenced key."""
        # Demo: strip marker
        marker_end = ciphertext.find(b":", len(b"ENC:"))
        if marker_end == -1:
            raise ValueError("Invalid encrypted blob")
        second_colon = ciphertext.find(b":", marker_end + 1)
        return ciphertext[second_colon + 1:]


# ---------------------------------------------------------------------------
# Evidence Store
# ---------------------------------------------------------------------------

class EvidenceStore:
    """
    Encrypted, audited document store for KYC evidence.

    Every operation is logged to an append-only access log.
    Documents are encrypted with envelope encryption (data key
    encrypted by master key in KMS).
    """

    def __init__(
        self,
        storage_root: str = "/tmp/evidence-store",
        encryption_provider: Optional[EncryptionProvider] = None,
    ):
        self.storage_root = storage_root
        self.crypto = encryption_provider or EncryptionProvider()
        self._documents: dict[str, StoredDocument] = {}
        self._access_log: list[AccessLogEntry] = []
        os.makedirs(storage_root, exist_ok=True)

    # ── Store ──────────────────────────────────────────────

    def store(
        self,
        case_id: str,
        document_type: str,
        content: bytes,
        retention_years: int = 5,
        actor: str = "SYSTEM",
        actor_role: str = "SYSTEM",
    ) -> str:
        """
        Encrypt and store a document. Returns the document_id reference.
        """
        content_hash = hashlib.sha256(content).hexdigest()

        # Check for duplicate by hash
        existing = self._find_by_hash(case_id, content_hash)
        if existing:
            self._log_access(
                existing.document_id, case_id, actor, actor_role,
                "STORE_DEDUP", details={"content_hash": content_hash},
            )
            return existing.document_id

        # Encrypt
        ciphertext, key_ref = self.crypto.encrypt(content)

        # Create storage path
        doc_id = str(uuid.uuid4())
        case_dir = os.path.join(self.storage_root, case_id)
        os.makedirs(case_dir, exist_ok=True)
        blob_path = os.path.join(case_dir, f"{doc_id}.enc")

        # Write encrypted blob
        with open(blob_path, "wb") as f:
            f.write(ciphertext)

        # Retention calculation
        retention_date = datetime.now(timezone.utc) + timedelta(
            days=retention_years * 365
        )

        doc = StoredDocument(
            document_id=doc_id,
            case_id=case_id,
            document_type=document_type,
            content_hash=content_hash,
            encrypted_key_ref=key_ref,
            storage_path=blob_path,
            size_bytes=len(content),
            retention_until=retention_date.isoformat(),
        )
        self._documents[doc_id] = doc

        # Update manifest
        self._write_manifest(case_id)

        self._log_access(
            doc_id, case_id, actor, actor_role, "STORE",
            details={
                "content_hash": content_hash,
                "size_bytes": len(content),
                "retention_until": doc.retention_until,
            },
        )

        logger.info(
            "Document stored: doc=%s case=%s type=%s size=%d",
            doc_id, case_id, document_type, len(content),
        )
        return doc_id

    # ── Retrieve ───────────────────────────────────────────

    def retrieve(
        self,
        document_id: str,
        actor: str,
        actor_role: str,
    ) -> Optional[bytes]:
        """
        Decrypt and return document content. Logs the access.
        """
        doc = self._documents.get(document_id)
        if not doc or doc.deleted:
            self._log_access(
                document_id, "", actor, actor_role, "RETRIEVE",
                success=False,
                details={"reason": "not_found_or_deleted"},
            )
            return None

        # Read encrypted blob
        if not os.path.exists(doc.storage_path):
            self._log_access(
                document_id, doc.case_id, actor, actor_role, "RETRIEVE",
                success=False,
                details={"reason": "blob_missing"},
            )
            return None

        with open(doc.storage_path, "rb") as f:
            ciphertext = f.read()

        plaintext = self.crypto.decrypt(ciphertext, doc.encrypted_key_ref)

        self._log_access(
            document_id, doc.case_id, actor, actor_role, "RETRIEVE",
            details={"size_bytes": len(plaintext)},
        )
        return plaintext

    # ── Secure deletion (cryptographic erasure) ────────────

    def secure_delete(
        self,
        document_id: str,
        actor: str,
        actor_role: str,
        reason: str = "",
    ) -> bool:
        """
        Securely delete a document by:
        1. Overwriting the blob with random data
        2. Removing the file
        3. Destroying the encryption key reference
        4. Marking as deleted in metadata

        Returns True if deletion was performed.
        """
        doc = self._documents.get(document_id)
        if not doc or doc.deleted:
            return False

        # Check retention: cannot delete before retention period
        if doc.retention_until:
            retention_date = datetime.fromisoformat(doc.retention_until)
            now = datetime.now(timezone.utc)
            if now < retention_date:
                self._log_access(
                    document_id, doc.case_id, actor, actor_role, "DELETE",
                    success=False,
                    details={
                        "reason": "retention_not_expired",
                        "retention_until": doc.retention_until,
                    },
                )
                raise ValueError(
                    f"Cannot delete document {document_id}: retention "
                    f"expires {doc.retention_until}"
                )

        # Overwrite with random bytes
        if os.path.exists(doc.storage_path):
            size = os.path.getsize(doc.storage_path)
            with open(doc.storage_path, "wb") as f:
                f.write(os.urandom(size))
            os.remove(doc.storage_path)

        # Mark deleted
        doc.deleted = True
        doc.deleted_at = datetime.now(timezone.utc).isoformat()
        doc.encrypted_key_ref = "[DESTROYED]"

        self._log_access(
            document_id, doc.case_id, actor, actor_role, "DELETE",
            details={"reason": reason},
        )

        logger.info(
            "Document securely deleted: doc=%s case=%s reason=%s",
            document_id, doc.case_id, reason,
        )
        return True

    # ── Retention compliance ───────────────────────────────

    def find_retention_violations(self) -> list[dict[str, Any]]:
        """
        Find documents that have passed their retention date but
        are still stored (need deletion), or documents approaching
        retention expiry (need review).
        """
        now = datetime.now(timezone.utc)
        violations = []
        for doc in self._documents.values():
            if doc.deleted or not doc.retention_until:
                continue
            retention_date = datetime.fromisoformat(doc.retention_until)
            days_remaining = (retention_date - now).days

            if days_remaining < 0:
                violations.append({
                    "document_id": doc.document_id,
                    "case_id": doc.case_id,
                    "type": "OVERDUE_DELETION",
                    "retention_until": doc.retention_until,
                    "days_overdue": abs(days_remaining),
                })
            elif days_remaining < 90:
                violations.append({
                    "document_id": doc.document_id,
                    "case_id": doc.case_id,
                    "type": "APPROACHING_EXPIRY",
                    "retention_until": doc.retention_until,
                    "days_remaining": days_remaining,
                })
        return violations

    # ── Export for regulatory requests ─────────────────────

    def export_case_documents(
        self,
        case_id: str,
        actor: str,
        actor_role: str,
    ) -> list[dict[str, Any]]:
        """Export all document metadata for a case (regulatory request)."""
        docs = [
            d for d in self._documents.values()
            if d.case_id == case_id and not d.deleted
        ]

        self._log_access(
            "", case_id, actor, actor_role, "EXPORT",
            details={"document_count": len(docs)},
        )

        return [asdict(d) for d in docs]

    # ── Access log queries ─────────────────────────────────

    def get_access_log(
        self,
        case_id: Optional[str] = None,
        document_id: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query the access log with optional filters."""
        results = []
        for entry in reversed(self._access_log):
            if case_id and entry.case_id != case_id:
                continue
            if document_id and entry.document_id != document_id:
                continue
            if actor and entry.actor != actor:
                continue
            results.append(asdict(entry))
            if len(results) >= limit:
                break
        return results

    # ── Internal helpers ───────────────────────────────────

    def _find_by_hash(
        self, case_id: str, content_hash: str
    ) -> Optional[StoredDocument]:
        for doc in self._documents.values():
            if (
                doc.case_id == case_id
                and doc.content_hash == content_hash
                and not doc.deleted
            ):
                return doc
        return None

    def _write_manifest(self, case_id: str) -> None:
        case_docs = [
            asdict(d)
            for d in self._documents.values()
            if d.case_id == case_id and not d.deleted
        ]
        manifest_path = os.path.join(
            self.storage_root, case_id, "manifest.json"
        )
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w") as f:
            json.dump(
                {"case_id": case_id, "documents": case_docs},
                f, indent=2,
            )

    def _log_access(
        self,
        document_id: str,
        case_id: str,
        actor: str,
        actor_role: str,
        action: str,
        success: bool = True,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        entry = AccessLogEntry(
            document_id=document_id,
            case_id=case_id,
            actor=actor,
            actor_role=actor_role,
            action=action,
            success=success,
            details=details or {},
        )
        self._access_log.append(entry)
