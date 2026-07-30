# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PSP Router with automatic failover.

Routing logic:
  1. Select the primary PSP for the given payment method + country combination.
  2. Attempt the deposit/withdrawal.
  3. On failure (network error, hard decline, PSP health failure) fall through
     to the configured fallback PSP list in order.
  4. All attempts are recorded in the PaymentProviderInfo.

Design is intentionally synchronous at the selection layer — the actual PSP
calls are async and are awaited inside the route() coroutine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from models import Deposit, PaymentMethod, PaymentStatus, PSPResponse, Withdrawal
from psp.base import PSPAdapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing rule
# ---------------------------------------------------------------------------


@dataclass
class RoutingRule:
    """Binds a payment method + country combination to an ordered list of PSPs."""

    method: PaymentMethod
    country_code: str             # ISO 3166-1 alpha-2, or "*" for wildcard
    primary: str                  # PSP name
    fallbacks: list[str] = field(default_factory=list)

    def matches(self, method: PaymentMethod, country: str) -> bool:
        method_match = self.method == method
        country_match = self.country_code == "*" or self.country_code == country.upper()
        return method_match and country_match


# ---------------------------------------------------------------------------
# PSP Registry
# ---------------------------------------------------------------------------


class PSPRegistry:
    """
    Holds all registered PSP adapters by name.
    Adapters are registered at application startup.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, PSPAdapter] = {}

    def register(self, adapter: PSPAdapter) -> None:
        self._adapters[adapter.name] = adapter
        logger.info("Registered PSP adapter: %s", adapter.name)

    def get(self, name: str) -> Optional[PSPAdapter]:
        return self._adapters.get(name)

    def names(self) -> list[str]:
        return list(self._adapters.keys())


# ---------------------------------------------------------------------------
# PSP Router
# ---------------------------------------------------------------------------


class PSPRouter:
    """
    Routes payment requests to the correct PSP with ordered failover.

    Example usage::

        router = PSPRouter(registry)
        router.add_rule(RoutingRule(PaymentMethod.CARD, "*", primary="adyen", fallbacks=["braintree"]))
        router.add_rule(RoutingRule(PaymentMethod.PAYPAL, "*", primary="paypal"))
        router.add_rule(RoutingRule(PaymentMethod.PIX, "BR", primary="pix"))

        response = await router.route_deposit(deposit)
    """

    def __init__(self, registry: PSPRegistry) -> None:
        self._registry = registry
        self._rules: list[RoutingRule] = []

    def add_rule(self, rule: RoutingRule) -> None:
        self._rules.append(rule)

    def _find_rule(self, method: PaymentMethod, country: str) -> Optional[RoutingRule]:
        # More specific country rules take precedence over wildcards
        specific = next(
            (r for r in self._rules if r.matches(method, country) and r.country_code != "*"),
            None,
        )
        if specific:
            return specific
        return next(
            (r for r in self._rules if r.matches(method, "*")),
            None,
        )

    def _get_candidate_psps(self, method: PaymentMethod, country: str) -> list[str]:
        rule = self._find_rule(method, country)
        if not rule:
            return []
        return [rule.primary] + rule.fallbacks

    async def route_deposit(self, deposit: Deposit) -> tuple[PSPResponse, str]:
        """
        Route a deposit to the best available PSP.

        Returns a (PSPResponse, psp_name) tuple.
        Raises RuntimeError if no PSP is configured for the method/country.
        """
        candidates = self._get_candidate_psps(deposit.method, deposit.country_code)
        if not candidates:
            raise RuntimeError(
                f"No PSP configured for method={deposit.method.value} country={deposit.country_code}"
            )

        last_response: Optional[PSPResponse] = None
        for psp_name in candidates:
            adapter = self._registry.get(psp_name)
            if adapter is None:
                logger.warning("PSP %s configured but not registered — skipping", psp_name)
                continue

            if not adapter.health_check():
                logger.warning("PSP %s health check failed — skipping", psp_name)
                continue

            logger.info(
                "Attempting deposit %s via PSP %s", deposit.payment_id, psp_name
            )
            try:
                response = await adapter.deposit(deposit)
                if response.success or response.status not in _RETRYABLE_STATUSES:
                    return response, psp_name
                logger.warning(
                    "PSP %s returned non-retryable failure for %s: %s",
                    psp_name,
                    deposit.payment_id,
                    response.error_code,
                )
                last_response = response
            except Exception as exc:
                logger.exception("PSP %s threw an exception for %s", psp_name, deposit.payment_id)
                last_response = PSPResponse(
                    success=False,
                    status=PaymentStatus.FAILED,
                    error_code="PSP_EXCEPTION",
                    error_message=str(exc),
                )

        # All candidates exhausted
        if last_response:
            return last_response, "none"
        return (
            PSPResponse(
                success=False,
                status=PaymentStatus.FAILED,
                error_code="NO_PSP_AVAILABLE",
                error_message="All PSPs exhausted",
            ),
            "none",
        )

    async def route_withdrawal(self, withdrawal: Withdrawal) -> tuple[PSPResponse, str]:
        """Route a withdrawal to a PSP that supports payouts."""
        candidates = self._get_candidate_psps(withdrawal.method, withdrawal.currency[:2])
        if not candidates:
            raise RuntimeError(f"No PSP configured for withdrawal method={withdrawal.method.value}")

        for psp_name in candidates:
            adapter = self._registry.get(psp_name)
            if adapter is None or not adapter.supports_withdrawals:
                continue
            try:
                response = await adapter.withdraw(withdrawal)
                return response, psp_name
            except Exception as exc:
                logger.exception(
                    "PSP %s withdrawal failed for %s", psp_name, withdrawal.withdrawal_id
                )
        return (
            PSPResponse(
                success=False,
                status=PaymentStatus.FAILED,
                error_code="WITHDRAWAL_FAILED",
                error_message="No PSP could process withdrawal",
            ),
            "none",
        )


# Statuses that warrant trying the next PSP in the failover chain
_RETRYABLE_STATUSES: set[PaymentStatus] = {
    PaymentStatus.FAILED,
}
