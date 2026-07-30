# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 34: War Stories
War Story 2: The Payment Gateway Cascade Failure - Payment Processor Examples

This file contains both the flawed failover logic that caused a real €2.3M
cascade failure, and the resilient circuit-breaker implementation that replaced it.
Preserved exactly as-is for educational purposes.

DO NOT use the PaymentProcessorFailover class in production.
"""

import asyncio
import logging
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# THE PROBLEMATIC CODE (preserved for educational reference)
# ---------------------------------------------------------------------------

class PaymentProcessorFailover:
    """
    FLAWED failover implementation that caused a cascade failure.
    DO NOT USE IN PRODUCTION.

    Problems:
    - No exponential backoff between retries
    - Immediate retry on any failure causing load avalanche
    - No circuit breaker pattern - failed processors keep receiving traffic
    - No per-processor health tracking
    """
    def __init__(self):
        self.processors = {
            'stripe': None,   # StripeProcessor()
            'braintree': None,  # BraintreeProcessor()
            'adyen': None,    # AdyenProcessor()
            'paypal': None    # PayPalProcessor()
        }
        self.failure_counts = {}
        self.circuit_breaker_timeout = 60  # seconds

    def process_payment(self, payment_data):
        for name, processor in self.processors.items():
            try:
                # BUG: No exponential backoff
                result = processor.charge(payment_data)  # ty:ignore[unresolved-attribute]

                # BUG: Immediate retry on any failure
                if not result['success']:
                    continue  # Try next processor immediately

                return result

            except Exception as e:
                # BUG: No circuit breaker pattern
                self.failure_counts[name] = self.failure_counts.get(name, 0) + 1
                continue

        # All processors failed
        raise Exception("All payment processors unavailable")


# ---------------------------------------------------------------------------
# THE FIXED CODE
# ---------------------------------------------------------------------------

class ResilientPaymentProcessor:
    def __init__(self):
        self.processors = {}
        self.circuit_breakers = {}
        self.health_checks = {}
        self.load_balancer = None  # IntelligentLoadBalancer()
        self.redis = None  # Set during initialization

    async def process_payment_with_resilience(self, payment_data):
        """Process payment with circuit breaker and intelligent routing"""
        available_processors = await self.get_healthy_processors()

        if not available_processors:
            # Emergency mode: queue payments for later processing
            await self.queue_payment_for_retry(payment_data)
            return {'status': 'queued', 'estimated_processing': '30_minutes'}

        # Route based on current load and success rates
        processor = self.load_balancer.select_processor(available_processors)  # ty:ignore[unresolved-attribute]

        try:
            result = await asyncio.wait_for(
                processor.charge(payment_data),
                timeout=10.0  # 10 second timeout
            )

            # Update success metrics
            await self.update_processor_metrics(processor.name, 'success')

            return result

        except asyncio.TimeoutError:
            await self.update_processor_metrics(processor.name, 'timeout')
            # Try one more processor before failing
            return await self.try_backup_processor(payment_data, available_processors)

        except Exception as e:
            await self.update_processor_metrics(processor.name, 'error')
            raise

    async def get_healthy_processors(self):
        """Get processors that are currently healthy"""
        healthy = []

        for name, processor in self.processors.items():
            circuit_breaker = self.circuit_breakers[name]

            if not circuit_breaker.is_open():
                # Perform health check
                if await self.health_checks[name]():
                    healthy.append(processor)

        return healthy

    async def update_processor_metrics(self, processor_name, result_type):
        """Update processor performance metrics"""
        key = f"processor_metrics:{processor_name}"
        await self.redis.hincrby(key, result_type, 1)  # ty:ignore[unresolved-attribute]
        await self.redis.hincrby(key, 'total_requests', 1)  # ty:ignore[unresolved-attribute]

        # Update success rate
        total = await self.redis.hget(key, 'total_requests')  # ty:ignore[unresolved-attribute]
        successes = await self.redis.hget(key, 'success')  # ty:ignore[unresolved-attribute]

        if total and successes:
            success_rate = int(successes) / int(total)
            await self.redis.hset(key, 'success_rate', success_rate)  # ty:ignore[unresolved-attribute]

            # Open circuit breaker if success rate drops below threshold
            if success_rate < 0.8:  # 80% success rate
                self.circuit_breakers[processor_name].record_failure()

    async def queue_payment_for_retry(self, payment_data):
        """Queue payment for later retry when processors recover"""
        # Implementation would persist to durable queue
        pass

    async def try_backup_processor(self, payment_data, available_processors):
        """Try a backup processor on timeout"""
        # Implementation would select from remaining healthy processors
        pass
