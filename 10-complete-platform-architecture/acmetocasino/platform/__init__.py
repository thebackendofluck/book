# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
platform — Infrastructure Utilities
=====================================

Cross-cutting infrastructure concerns shared by every service in the
acmetocasino platform.  None of these modules contain domain logic.

``config``
    Hierarchical configuration with environment variable support and
    brand-level overrides.

``database``
    Async database abstraction (SQLAlchemy-style interface) with an
    in-memory back-end for tests.

``retry``
    Decorator-based retry with configurable exponential backoff, jitter,
    and per-exception-type filtering.

``id_factory``
    ID generation utilities: UUID7 for time-ordered IDs and compact
    Base32-encoded short IDs for display in URLs.

``feature_flags``
    Simple feature flag system with brand and jurisdiction scoping.
    Supports gradual rollouts and A/B experiments.
"""

from __future__ import annotations

from acmetocasino.platform.config import BrandConfig, PlatformConfig
from acmetocasino.platform.database import DatabaseAdapter, InMemoryDatabaseAdapter
from acmetocasino.platform.feature_flags import FeatureFlag, FeatureFlagRegistry
from acmetocasino.platform.id_factory import IdFactory
from acmetocasino.platform.retry import RetryConfig, with_retry

__all__ = [
    "BrandConfig",
    "DatabaseAdapter",
    "FeatureFlag",
    "FeatureFlagRegistry",
    "IdFactory",
    "InMemoryDatabaseAdapter",
    "PlatformConfig",
    "RetryConfig",
    "with_retry",
]
