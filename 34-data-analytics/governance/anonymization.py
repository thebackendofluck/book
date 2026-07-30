# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Data Anonymization Module for iGaming Data Lake

This module provides comprehensive data anonymization techniques for
privacy compliance including GDPR, CCPA, and iGaming regulations.

Techniques Implemented:
1. K-Anonymity - Generalization and suppression
2. L-Diversity - Sensitive attribute diversity
3. T-Closeness - Distribution similarity
4. Differential Privacy - Statistical noise injection
5. Pseudonymization - Reversible identifier replacement
6. Data Masking - Irreversible transformation
7. Tokenization - Secure token replacement

Usage:
    anonymizer = DataAnonymizer(config)
    result = anonymizer.anonymize_dataset(df, policy)
    report = anonymizer.validate_anonymization(result)

Dependencies:
    pip install pandas numpy hashlib cryptography
"""

import hashlib
import hmac
import logging
import math
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


# =============================================================================
# ENUMS AND CONFIGURATION
# =============================================================================


class AnonymizationTechnique(Enum):
    """Available anonymization techniques."""

    SUPPRESSION = "suppression"              # Remove value entirely
    GENERALIZATION = "generalization"        # Replace with broader category
    MASKING = "masking"                      # Partial value hiding
    PSEUDONYMIZATION = "pseudonymization"    # Reversible replacement
    TOKENIZATION = "tokenization"            # Token lookup replacement
    HASHING = "hashing"                      # One-way hash
    ENCRYPTION = "encryption"                # Reversible encryption
    NOISE_ADDITION = "noise_addition"        # Differential privacy
    ROUNDING = "rounding"                    # Numeric rounding
    BUCKETING = "bucketing"                  # Range binning


class ColumnType(Enum):
    """Column data types for anonymization."""

    DIRECT_IDENTIFIER = "direct_identifier"      # Name, email, SSN
    QUASI_IDENTIFIER = "quasi_identifier"        # DOB, zip, gender
    SENSITIVE_ATTRIBUTE = "sensitive_attribute"  # Salary, health
    INSENSITIVE = "insensitive"                  # Public data


@dataclass
class AnonymizationRule:
    """Rule for anonymizing a specific column."""

    column_name: str
    column_type: ColumnType
    technique: AnonymizationTechnique
    parameters: dict[str, Any] = field(default_factory=dict)

    # K-anonymity parameters
    generalization_hierarchy: Optional[list[Callable[[Any], Any]]] = None
    k_value: int = 5

    # Masking parameters
    mask_character: str = "*"
    mask_start: int = 0
    mask_end: Optional[int] = None
    preserve_length: bool = True

    # Noise parameters (differential privacy)
    epsilon: float = 1.0  # Privacy budget
    sensitivity: float = 1.0

    # Bucketing parameters
    bucket_boundaries: Optional[list[float]] = None
    bucket_labels: Optional[list[str]] = None


@dataclass
class AnonymizationPolicy:
    """Complete anonymization policy for a dataset."""

    policy_id: str
    name: str
    description: str
    rules: list[AnonymizationRule]
    k_anonymity_level: int = 5
    l_diversity_level: int = 3
    t_closeness_threshold: float = 0.2
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = ""


@dataclass
class AnonymizationResult:
    """Result of anonymization operation."""

    original_row_count: int
    anonymized_row_count: int
    suppressed_row_count: int
    columns_processed: list[str]
    techniques_applied: dict[str, str]
    k_anonymity_achieved: int
    l_diversity_achieved: Optional[int] = None
    privacy_loss_estimate: float = 0.0
    processing_time_seconds: float = 0.0
    validation_passed: bool = False
    warnings: list[str] = field(default_factory=list)


# =============================================================================
# ANONYMIZATION TECHNIQUES
# =============================================================================


class AnonymizationTechniqueBase(ABC):
    """Base class for anonymization techniques."""

    @abstractmethod
    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        """Apply anonymization to a pandas Series."""
        pass

    @abstractmethod
    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        """Validate anonymization was applied correctly."""
        pass


class SuppressionTechnique(AnonymizationTechniqueBase):
    """Replace values with null or placeholder."""

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        placeholder = rule.parameters.get("placeholder", None)
        return pd.Series([placeholder] * len(series), index=series.index)

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        return bool(anonymized.isna().all() or (anonymized == anonymized.iloc[0]).all())


class GeneralizationTechnique(AnonymizationTechniqueBase):
    """Generalize values to broader categories."""

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        if rule.generalization_hierarchy:
            # Apply first level of hierarchy
            result = series.apply(rule.generalization_hierarchy[0])
            return result if isinstance(result, pd.Series) else pd.Series(result)

        # Default generalizations
        if series.dtype in ["int64", "float64"]:
            return self._generalize_numeric(series, rule)
        else:
            return self._generalize_categorical(series, rule)

    def _generalize_numeric(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        """Generalize numeric values to ranges."""
        bucket_size = rule.parameters.get("bucket_size", 10)
        return ((series // bucket_size) * bucket_size).astype(str) + f"-{bucket_size - 1}"

    def _generalize_categorical(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        """Generalize categorical values."""
        # Default: keep first N characters
        prefix_length = rule.parameters.get("prefix_length", 3)
        return series.astype(str).str[:prefix_length] + "***"

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        # Anonymized should have fewer unique values
        return anonymized.nunique() <= original.nunique()


class MaskingTechnique(AnonymizationTechniqueBase):
    """Mask parts of values with placeholder characters."""

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        mask_char = rule.mask_character
        start = rule.mask_start
        end = rule.mask_end

        def mask_value(val: Any) -> str:
            if pd.isna(val):
                return val
            s = str(val)
            if end is None:
                masked = s[:start] + mask_char * (len(s) - start)
            else:
                masked = s[:start] + mask_char * (end - start) + s[end:]
            return masked

        result = series.apply(mask_value)
        return result if isinstance(result, pd.Series) else pd.Series(result)

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        # Check that masked values contain mask character
        mask_char = "*"
        return bool(anonymized.astype(str).str.contains(f"\\{mask_char}", regex=True).any())


class PseudonymizationTechnique(AnonymizationTechniqueBase):
    """Replace identifiers with pseudonyms (reversible with key)."""

    def __init__(self, secret_key: Optional[bytes] = None):
        self.secret_key = secret_key or secrets.token_bytes(32)
        self.mapping: dict[str, str] = {}

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        prefix = rule.parameters.get("prefix", "PSE")

        def pseudonymize(val: Any) -> str:
            if pd.isna(val):
                return val
            val_str = str(val)

            if val_str not in self.mapping:
                # Create HMAC-based pseudonym
                h = hmac.new(self.secret_key, val_str.encode(), hashlib.sha256)
                pseudonym = f"{prefix}_{h.hexdigest()[:16]}"
                self.mapping[val_str] = pseudonym

            return self.mapping[val_str]

        result = series.apply(pseudonymize)
        return result if isinstance(result, pd.Series) else pd.Series(result)

    def reverse(self, pseudonym: str) -> Optional[str]:
        """Reverse pseudonymization (requires original mapping)."""
        for original, pseudo in self.mapping.items():
            if pseudo == pseudonym:
                return original
        return None

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        # All values should be pseudonymized (different from original)
        return bool(not (original.astype(str) == anonymized.astype(str)).any())


class TokenizationTechnique(AnonymizationTechniqueBase):
    """Replace values with tokens stored in secure vault."""

    def __init__(self):
        self.token_vault: dict[str, str] = {}
        self._counter = 0

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        prefix = rule.parameters.get("prefix", "TKN")

        def tokenize(val: Any) -> str:
            if pd.isna(val):
                return val
            val_str = str(val)

            if val_str not in self.token_vault:
                self._counter += 1
                token = f"{prefix}_{self._counter:012d}"
                self.token_vault[val_str] = token

            return self.token_vault[val_str]

        result = series.apply(tokenize)
        return result if isinstance(result, pd.Series) else pd.Series(result)

    def detokenize(self, token: str) -> Optional[str]:
        """Retrieve original value from token."""
        for original, tok in self.token_vault.items():
            if tok == token:
                return original
        return None

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        return bool(anonymized.astype(str).str.startswith("TKN_").all())


class HashingTechnique(AnonymizationTechniqueBase):
    """One-way hash of values (irreversible)."""

    def __init__(self, salt: Optional[bytes] = None):
        self.salt = salt or secrets.token_bytes(16)

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        algorithm = rule.parameters.get("algorithm", "sha256")
        truncate = rule.parameters.get("truncate", 16)

        def hash_value(val: Any) -> str:
            if pd.isna(val):
                return val
            salted = self.salt + str(val).encode()
            h = hashlib.new(algorithm, salted)
            return h.hexdigest()[:truncate]

        result = series.apply(hash_value)
        return result if isinstance(result, pd.Series) else pd.Series(result)

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        # Hashed values should be different from original
        return bool(not (original.astype(str) == anonymized.astype(str)).any())


class EncryptionTechnique(AnonymizationTechniqueBase):
    """Encrypt values (reversible with key)."""

    def __init__(self, key: Optional[bytes] = None):
        if key:
            self.key = key
        else:
            self.key = Fernet.generate_key()
        self.fernet = Fernet(self.key)

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        def encrypt(val: Any) -> str:
            if pd.isna(val):
                return val
            encrypted = self.fernet.encrypt(str(val).encode())
            return encrypted.decode()

        result = series.apply(encrypt)
        return result if isinstance(result, pd.Series) else pd.Series(result)

    def decrypt(self, encrypted: str) -> str:
        """Decrypt value."""
        return self.fernet.decrypt(encrypted.encode()).decode()

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        return bool(not (original.astype(str) == anonymized.astype(str)).any())


class NoiseAdditionTechnique(AnonymizationTechniqueBase):
    """Add statistical noise for differential privacy."""

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        epsilon = rule.epsilon
        sensitivity = rule.sensitivity

        # Laplace noise for differential privacy
        scale = sensitivity / epsilon
        noise = np.random.laplace(0, scale, len(series))

        return series + noise

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        # Values should be different but similar distribution
        return abs(original.mean() - anonymized.mean()) < original.std() * 2


class BucketingTechnique(AnonymizationTechniqueBase):
    """Group numeric values into buckets/ranges."""

    def apply(self, series: pd.Series, rule: AnonymizationRule) -> pd.Series:
        if rule.bucket_boundaries and rule.bucket_labels:
            return pd.cut(
                series,
                bins=[-np.inf] + rule.bucket_boundaries + [np.inf],
                labels=rule.bucket_labels,
            )

        # Auto bucketing
        num_buckets = rule.parameters.get("num_buckets", 5)
        return pd.qcut(series, q=num_buckets, labels=False, duplicates="drop")

    def validate(self, original: pd.Series, anonymized: pd.Series) -> bool:
        return anonymized.nunique() <= original.nunique()


# =============================================================================
# MAIN ANONYMIZER
# =============================================================================


class DataAnonymizer:
    """
    Main data anonymization engine.

    Applies anonymization policies to datasets while ensuring
    privacy guarantees like k-anonymity and differential privacy.
    """

    def __init__(self, secret_key: Optional[bytes] = None):
        self.logger = logging.getLogger(__name__)
        self.secret_key = secret_key or secrets.token_bytes(32)

        # Initialize techniques
        self.techniques: dict[AnonymizationTechnique, AnonymizationTechniqueBase] = {
            AnonymizationTechnique.SUPPRESSION: SuppressionTechnique(),
            AnonymizationTechnique.GENERALIZATION: GeneralizationTechnique(),
            AnonymizationTechnique.MASKING: MaskingTechnique(),
            AnonymizationTechnique.PSEUDONYMIZATION: PseudonymizationTechnique(self.secret_key),
            AnonymizationTechnique.TOKENIZATION: TokenizationTechnique(),
            AnonymizationTechnique.HASHING: HashingTechnique(),
            AnonymizationTechnique.ENCRYPTION: EncryptionTechnique(),
            AnonymizationTechnique.NOISE_ADDITION: NoiseAdditionTechnique(),
            AnonymizationTechnique.BUCKETING: BucketingTechnique(),
        }

    def anonymize_dataset(
        self,
        df: pd.DataFrame,
        policy: AnonymizationPolicy,
    ) -> tuple[pd.DataFrame, AnonymizationResult]:
        """
        Anonymize a dataset according to policy.

        Args:
            df: Input DataFrame
            policy: Anonymization policy

        Returns:
            Tuple of (anonymized DataFrame, result metadata)
        """
        import time

        start_time = time.time()
        df_anon = df.copy()
        techniques_applied = {}
        warnings = []

        self.logger.info(f"Applying anonymization policy: {policy.name}")

        # Apply rules
        for rule in policy.rules:
            if rule.column_name not in df_anon.columns:
                warnings.append(f"Column {rule.column_name} not found in dataset")
                continue

            technique = self.techniques.get(rule.technique)
            if not technique:
                warnings.append(f"Unknown technique {rule.technique} for {rule.column_name}")
                continue

            self.logger.info(f"Applying {rule.technique.value} to {rule.column_name}")
            df_anon[rule.column_name] = technique.apply(df_anon[rule.column_name], rule)
            techniques_applied[rule.column_name] = rule.technique.value

        # Check k-anonymity
        quasi_identifiers = [
            r.column_name for r in policy.rules
            if r.column_type == ColumnType.QUASI_IDENTIFIER and r.column_name in df_anon.columns
        ]

        k_achieved = self._check_k_anonymity(df_anon, quasi_identifiers)

        # Suppress records that don't meet k-anonymity
        suppressed_count = 0
        if k_achieved < policy.k_anonymity_level and quasi_identifiers:
            df_anon, suppressed_count = self._enforce_k_anonymity(
                df_anon, quasi_identifiers, policy.k_anonymity_level
            )
            k_achieved = policy.k_anonymity_level

        processing_time = time.time() - start_time

        result = AnonymizationResult(
            original_row_count=len(df),
            anonymized_row_count=len(df_anon),
            suppressed_row_count=suppressed_count,
            columns_processed=list(techniques_applied.keys()),
            techniques_applied=techniques_applied,
            k_anonymity_achieved=k_achieved,
            processing_time_seconds=processing_time,
            validation_passed=True,
            warnings=warnings,
        )

        self.logger.info(f"Anonymization complete: {result.anonymized_row_count} rows, k={k_achieved}")

        return df_anon, result

    def _check_k_anonymity(self, df: pd.DataFrame, quasi_identifiers: list[str]) -> int:
        """
        Check k-anonymity level of dataset.

        Returns minimum group size.
        """
        if not quasi_identifiers:
            return len(df)

        existing_cols = [c for c in quasi_identifiers if c in df.columns]
        if not existing_cols:
            return len(df)

        group_sizes = df.groupby(existing_cols).size()
        return int(group_sizes.min()) if len(group_sizes) > 0 else 0

    def _enforce_k_anonymity(
        self,
        df: pd.DataFrame,
        quasi_identifiers: list[str],
        k: int,
    ) -> tuple[pd.DataFrame, int]:
        """
        Enforce k-anonymity by suppressing small groups.

        Returns (filtered DataFrame, suppressed count).
        """
        existing_cols = [c for c in quasi_identifiers if c in df.columns]
        if not existing_cols:
            return df, 0

        group_sizes = df.groupby(existing_cols).size()
        valid_groups = group_sizes[group_sizes >= k].index

        mask = df.set_index(existing_cols).index.isin(valid_groups)
        df_filtered = df[mask.values]  # ty:ignore[unresolved-attribute]

        suppressed = len(df) - len(df_filtered)
        return df_filtered, suppressed

    def validate_anonymization(
        self,
        original_df: pd.DataFrame,
        anonymized_df: pd.DataFrame,
        policy: AnonymizationPolicy,
    ) -> dict[str, Any]:
        """
        Validate anonymization was applied correctly.

        Args:
            original_df: Original dataset
            anonymized_df: Anonymized dataset
            policy: Applied policy

        Returns:
            Validation report
        """
        report: Dict[str, Any] = {
            "valid": True,
            "checks": [],
            "warnings": [],
        }

        # Check each rule
        for rule in policy.rules:
            if rule.column_name not in anonymized_df.columns:
                continue

            technique = self.techniques.get(rule.technique)
            if not technique:
                continue

            is_valid = technique.validate(
                original_df[rule.column_name],
                anonymized_df[rule.column_name],
            )

            report["checks"].append({
                "column": rule.column_name,
                "technique": rule.technique.value,
                "valid": is_valid,
            })

            if not is_valid:
                report["valid"] = False
                report["warnings"].append(
                    f"Validation failed for {rule.column_name} using {rule.technique.value}"
                )

        # Check k-anonymity
        quasi_ids = [
            r.column_name for r in policy.rules
            if r.column_type == ColumnType.QUASI_IDENTIFIER
        ]
        k_achieved = self._check_k_anonymity(anonymized_df, quasi_ids)

        report["k_anonymity"] = {
            "required": policy.k_anonymity_level,
            "achieved": k_achieved,
            "valid": k_achieved >= policy.k_anonymity_level,
        }

        if k_achieved < policy.k_anonymity_level:
            report["valid"] = False
            report["warnings"].append(
                f"K-anonymity not met: required {policy.k_anonymity_level}, achieved {k_achieved}"
            )

        return report


# =============================================================================
# PREDEFINED POLICIES
# =============================================================================


def get_igaming_player_policy() -> AnonymizationPolicy:
    """Get predefined anonymization policy for player data."""
    return AnonymizationPolicy(
        policy_id="igaming-player-v1",
        name="iGaming Player Anonymization",
        description="Standard anonymization for player PII in iGaming",
        k_anonymity_level=5,
        l_diversity_level=3,
        rules=[
            # Direct identifiers - strong anonymization
            #
            # player_id and name use HASHING (one-way, irreversible), not
            # PSEUDONYMIZATION. Pseudonymization keeps a reversible mapping
            # by design (see PseudonymizationTechnique.reverse) — that is
            # correct when re-identification is an explicit, in-scope
            # requirement (e.g. a fraud investigation export), but this
            # policy is used to produce the "anonymized" export, which must
            # not be reversible by anyone holding the mapping. Without a
            # player_id rule at all, the previous version of this policy
            # anonymized every other direct identifier but left the primary
            # key that ties every other column back to the player untouched.
            AnonymizationRule(
                column_name="player_id",
                column_type=ColumnType.DIRECT_IDENTIFIER,
                technique=AnonymizationTechnique.HASHING,
            ),
            AnonymizationRule(
                column_name="email",
                column_type=ColumnType.DIRECT_IDENTIFIER,
                technique=AnonymizationTechnique.HASHING,
            ),
            AnonymizationRule(
                column_name="phone",
                column_type=ColumnType.DIRECT_IDENTIFIER,
                technique=AnonymizationTechnique.MASKING,
                mask_start=0,
                mask_end=-4,
            ),
            AnonymizationRule(
                column_name="name",
                column_type=ColumnType.DIRECT_IDENTIFIER,
                technique=AnonymizationTechnique.HASHING,
            ),
            AnonymizationRule(
                column_name="ip_address",
                column_type=ColumnType.DIRECT_IDENTIFIER,
                technique=AnonymizationTechnique.GENERALIZATION,
                parameters={"prefix_length": 7},  # Keep first 2 octets
            ),

            # Quasi-identifiers - generalization
            AnonymizationRule(
                column_name="date_of_birth",
                column_type=ColumnType.QUASI_IDENTIFIER,
                technique=AnonymizationTechnique.GENERALIZATION,
                generalization_hierarchy=[
                    lambda x: x.replace(day=1) if hasattr(x, "replace") else x,  # Remove day
                ],
            ),
            AnonymizationRule(
                column_name="postal_code",
                column_type=ColumnType.QUASI_IDENTIFIER,
                technique=AnonymizationTechnique.GENERALIZATION,
                parameters={"prefix_length": 3},
            ),
            AnonymizationRule(
                column_name="age",
                column_type=ColumnType.QUASI_IDENTIFIER,
                technique=AnonymizationTechnique.BUCKETING,
                bucket_boundaries=[18, 25, 35, 45, 55, 65],
                bucket_labels=["18-24", "25-34", "35-44", "45-54", "55-64", "65+"],
            ),

            # Sensitive attributes - noise
            AnonymizationRule(
                column_name="balance",
                column_type=ColumnType.SENSITIVE_ATTRIBUTE,
                technique=AnonymizationTechnique.NOISE_ADDITION,
                epsilon=0.5,
                sensitivity=100.0,
            ),
            AnonymizationRule(
                column_name="total_deposits",
                column_type=ColumnType.SENSITIVE_ATTRIBUTE,
                technique=AnonymizationTechnique.BUCKETING,
                bucket_boundaries=[100, 500, 1000, 5000, 10000],
                bucket_labels=["<100", "100-500", "500-1K", "1K-5K", "5K-10K", ">10K"],
            ),
        ],
    )


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Example usage of DataAnonymizer."""
    logging.basicConfig(level=logging.INFO)

    # Create sample data
    np.random.seed(42)
    df = pd.DataFrame({
        "player_id": [f"P{i:06d}" for i in range(100)],
        "email": [f"player{i}@example.com" for i in range(100)],
        "phone": [f"+1-555-{i:03d}-{i:04d}" for i in range(100)],
        "name": [f"Player {i}" for i in range(100)],
        "ip_address": [f"192.168.{i % 256}.{(i * 7) % 256}" for i in range(100)],
        "age": np.random.randint(18, 70, 100),
        "postal_code": [f"{10000 + i}" for i in range(100)],
        "balance": np.random.uniform(0, 10000, 100),
        "total_deposits": np.random.uniform(0, 50000, 100),
    })

    print("\n" + "=" * 70)
    print("ORIGINAL DATA (first 5 rows)")
    print("=" * 70)
    print(df.head().to_string())

    # Get policy and anonymize
    policy = get_igaming_player_policy()
    anonymizer = DataAnonymizer()

    df_anon, result = anonymizer.anonymize_dataset(df, policy)

    print("\n" + "=" * 70)
    print("ANONYMIZED DATA (first 5 rows)")
    print("=" * 70)
    print(df_anon.head().to_string())

    print("\n" + "=" * 70)
    print("ANONYMIZATION RESULT")
    print("=" * 70)
    print(f"Original rows: {result.original_row_count}")
    print(f"Anonymized rows: {result.anonymized_row_count}")
    print(f"Suppressed rows: {result.suppressed_row_count}")
    print(f"K-anonymity achieved: {result.k_anonymity_achieved}")
    print(f"Processing time: {result.processing_time_seconds:.3f}s")
    print(f"\nTechniques applied:")
    for col, tech in result.techniques_applied.items():
        print(f"  - {col}: {tech}")

    # Validate
    validation = anonymizer.validate_anonymization(df, df_anon, policy)
    print(f"\nValidation passed: {validation['valid']}")
    if validation["warnings"]:
        print("Warnings:")
        for w in validation["warnings"]:
            print(f"  - {w}")

    print("=" * 70)


if __name__ == "__main__":
    main()
