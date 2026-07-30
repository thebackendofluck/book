# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 38: Caching Strategies and Benefits
Enterprise caching patterns for iGaming platforms.

This module provides production-ready caching implementations including:
- Cache-aside (lazy loading) pattern
- Write-through caching
- Cache warming strategies
- Stampede prevention with distributed locking
- Cache sizing calculators
- ROI analysis tools

Usage:
    from cache_patterns import (
        CacheManager,
        CacheWarmer,
        StampedeSafeCache,
        CacheSizingCalculator,
        CacheROICalculator,
        CacheMonitor
    )
"""

from .cache_patterns import (  # ty:ignore[unresolved-import]
    CacheManager,
    CacheConfig,
    CacheResult,
    CacheStrategy,
)
from .cache_safety import (  # ty:ignore[unresolved-import]
    StampedeSafeCache,
    DistributedLock,
    LockConfig,
)
from .cache_warmer import (  # ty:ignore[unresolved-import]
    CacheWarmer,
    WarmingStrategy,
    WarmingResult,
)
from .cache_calculator import (  # ty:ignore[unresolved-import]
    CacheSizingCalculator,
    CacheROICalculator,
    SizingResult,
    ROIResult,
)
from .cache_monitor import (  # ty:ignore[unresolved-import]
    CacheMonitor,
    CacheMetrics,
    PerformanceReport,
)

__all__ = [
    # Core patterns
    "CacheManager",
    "CacheConfig",
    "CacheResult",
    "CacheStrategy",
    # Safety
    "StampedeSafeCache",
    "DistributedLock",
    "LockConfig",
    # Warming
    "CacheWarmer",
    "WarmingStrategy",
    "WarmingResult",
    # Calculators
    "CacheSizingCalculator",
    "CacheROICalculator",
    "SizingResult",
    "ROIResult",
    # Monitoring
    "CacheMonitor",
    "CacheMetrics",
    "PerformanceReport",
]

__version__ = "1.0.0"
