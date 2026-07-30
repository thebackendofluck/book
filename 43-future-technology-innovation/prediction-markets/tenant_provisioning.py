#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-tenant vertical provisioning for a B2B prediction-markets platform.

Chapter 43c reference implementation, Pattern 2 (the BetConstruct model).
A platform supplier does not hold a single licence -- it hosts dozens of
operator tenants, each carrying its *own* licence in its own jurisdiction.
Turning on a vertical like prediction markets is therefore never a single
global switch: it is a per-tenant decision that has to be re-validated
against that tenant's licence every time, because the jurisdiction map
keeps moving underneath the platform (see jurisdiction_gate.py).

This module also encodes the chapter 47b config-distribution problem in
miniature: every change to a tenant's vertical configuration bumps a
version counter, and ``distribute()`` hands back a full snapshot for
every registered tenant -- including tenants that were registered but
never opted in, whose config is the explicit "disabled" default rather
than an absence of data. A platform serving hundreds of tenants cannot
afford ambiguity between "not configured" and "configured off".

Fail-closed, all-or-nothing enabling
-------------------------------------
``enable_prediction_markets`` evaluates the jurisdiction gate for *every*
requested category before touching any state. If even one category is
denied for the tenant's jurisdiction, the whole request is rejected and
nothing is enabled -- a platform supplier that partially enables a
vertical because three of five categories passed the gate has shipped an
unlicensed product to a tenant by omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from jurisdiction_gate import AccessMode, JurisdictionGate, UnknownJurisdiction
from market_lifecycle import MarketCategory

__all__ = [
    "TenantLicence",
    "VerticalConfig",
    "ProvisioningError",
    "TenantProvisioner",
]


@dataclass(frozen=True)
class TenantLicence:
    tenant_id: str
    jurisdiction: str          # ISO 3166-1 alpha-2, must exist in gate policies
    licence_ref: str           # e.g. "MGA/B2C/123/2020"


@dataclass(frozen=True)
class VerticalConfig:
    enabled: bool
    categories: Tuple[MarketCategory, ...]
    fee_bps: int                       # platform trading fee, basis points
    partner_route: Optional[str] = None  # required partner under PARTNER_EMBEDDED


_DISABLED_CONFIG = VerticalConfig(
    enabled=False, categories=(), fee_bps=0, partner_route=None
)


class ProvisioningError(Exception):
    pass


class TenantProvisioner:
    """Registers tenants and gates their prediction-markets vertical."""

    def __init__(self, gate: JurisdictionGate, clock: Callable[[], float]):
        self.gate = gate
        self.clock = clock
        self._licences: Dict[str, TenantLicence] = {}
        self._configs: Dict[str, Tuple[int, VerticalConfig]] = {}
        self.audit_log: List[Tuple[float, str, str]] = []

    # -- registration ----------------------------------------------------

    def register_tenant(self, licence: TenantLicence) -> None:
        if licence.tenant_id in self._licences:
            raise ProvisioningError(
                f"tenant {licence.tenant_id!r} already registered"
            )
        # Raises UnknownJurisdiction (propagated, not wrapped) if the
        # jurisdiction has no policy -- the gate's default-deny stance
        # applies at registration time, not just at enable time.
        if licence.jurisdiction.upper() not in self.gate.policies:
            raise UnknownJurisdiction(
                f"no policy for {licence.jurisdiction!r}; default-deny applies"
            )
        self._licences[licence.tenant_id] = licence
        self._configs[licence.tenant_id] = (1, _DISABLED_CONFIG)
        self._log(
            licence.tenant_id,
            f"registered jurisdiction={licence.jurisdiction} "
            f"licence_ref={licence.licence_ref}",
        )

    # -- vertical toggle ---------------------------------------------------

    def enable_prediction_markets(
        self,
        tenant_id: str,
        categories,
        fee_bps: int,
        partner_route: Optional[str] = None,
    ) -> VerticalConfig:
        licence = self._require_tenant(tenant_id)
        if not 0 <= fee_bps <= 1000:
            raise ValueError(f"fee_bps must be in [0, 1000], got {fee_bps}")

        requested = tuple(categories)
        denied: List[str] = []
        required_partner: Optional[str] = None
        for category in requested:
            decision = self.gate.evaluate(licence.jurisdiction, category)
            if not decision.allowed:
                denied.append(category.value)
                continue
            if decision.mode == AccessMode.PARTNER_EMBEDDED:
                required_partner = decision.partner

        if denied:
            raise ProvisioningError(
                f"tenant {tenant_id!r}: jurisdiction {licence.jurisdiction} "
                f"denies category(ies): {', '.join(denied)}"
            )
        if required_partner is not None and partner_route != required_partner:
            raise ProvisioningError(
                f"tenant {tenant_id!r}: jurisdiction {licence.jurisdiction} "
                f"requires partner_route={required_partner!r}, "
                f"got {partner_route!r}"
            )

        config = VerticalConfig(
            enabled=True,
            categories=requested,
            fee_bps=fee_bps,
            partner_route=partner_route,
        )
        self._bump(tenant_id, config)
        self._log(
            tenant_id,
            f"enabled categories={[c.value for c in requested]} "
            f"fee_bps={fee_bps} partner_route={partner_route}",
        )
        return config

    def disable_prediction_markets(self, tenant_id: str) -> VerticalConfig:
        self._require_tenant(tenant_id)
        _, current = self._configs[tenant_id]
        config = VerticalConfig(
            enabled=False,
            categories=current.categories,
            fee_bps=current.fee_bps,
            partner_route=current.partner_route,
        )
        self._bump(tenant_id, config)
        self._log(tenant_id, "disabled")
        return config

    # -- distribution ------------------------------------------------------

    def config_for(self, tenant_id: str) -> Tuple[int, VerticalConfig]:
        self._require_tenant(tenant_id)
        return self._configs[tenant_id]

    def distribute(self) -> Dict[str, dict]:
        """Snapshot of every registered tenant's versioned config.

        Tenants that never called ``enable_prediction_markets`` are
        included with the explicit disabled default -- consumers of this
        feed must not have to special-case "missing" as "off".
        """
        return {
            tenant_id: {"version": version, "config": config}
            for tenant_id, (version, config) in self._configs.items()
        }

    # -- internals -----------------------------------------------------

    def _require_tenant(self, tenant_id: str) -> TenantLicence:
        licence = self._licences.get(tenant_id)
        if licence is None:
            raise ProvisioningError(f"unknown tenant {tenant_id!r}")
        return licence

    def _bump(self, tenant_id: str, config: VerticalConfig) -> None:
        version, _ = self._configs[tenant_id]
        self._configs[tenant_id] = (version + 1, config)

    def _log(self, tenant_id: str, event: str) -> None:
        self.audit_log.append((self.clock(), tenant_id, event))
