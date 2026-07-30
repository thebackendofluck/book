# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
registry_router.py — Route exclusion checks to the correct registry by jurisdiction.

Maps each Jurisdiction to the appropriate registry service and handles
the translation between the generic API models and the per-registry DTOs.

Jurisdiction → Registry mapping:
  GB (United Kingdom) → GamStop
  SE (Sweden)         → Spelpaus
  DK (Denmark)        → ROFUS
  BR (Brazil)         → Brazil National (BNAFAR)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import structlog

from brazil_registry import BrazilRegistryService
from gamstop import GamstopService
from models import (
    BrazilApiConfig,
    BrazilUser,
    ExclusionCheck,
    ExclusionStatus,
    GamstopApiConfig,
    GamstopUser,
    Jurisdiction,
    Registry,
    RegistrationRequest,
    RevocationRequest,
    RofusApiConfig,
    RofusUser,
    SpelpausApiConfig,
    SpelpausUser,
)
from rofus import RofusService
from spelpaus import SpelpausService

log = structlog.get_logger(__name__)


class RegistryRouter:
    """
    Routes exclusion check, registration, and revocation requests to the
    correct national registry based on jurisdiction.

    Instantiates service clients from environment variables on first use.
    """

    def __init__(self) -> None:
        self._gamstop: GamstopService | None = None
        self._spelpaus: SpelpausService | None = None
        self._rofus: RofusService | None = None
        self._brazil: BrazilRegistryService | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check(self, request: ExclusionCheck) -> ExclusionStatus:
        """
        Check a player against the appropriate national registry.

        The jurisdiction field on the request determines which service is called.
        """
        log.info("registry_router: check",
                 player_id=request.player_id,
                 jurisdiction=request.jurisdiction)

        jur = request.jurisdiction

        if jur == Jurisdiction.GB:
            return self._check_gamstop(request)
        elif jur == Jurisdiction.SE:
            return self._check_spelpaus(request)
        elif jur == Jurisdiction.DK:
            return self._check_rofus(request)
        elif jur == Jurisdiction.BR:
            return self._check_brazil(request)
        else:
            raise ValueError(f"Unsupported jurisdiction: {jur}")

    def register(self, request: RegistrationRequest) -> dict:
        """Register a player for self-exclusion (where API supports it)."""
        jur = request.jurisdiction
        if jur == Jurisdiction.BR:
            svc = self._get_brazil()
            user = BrazilUser(id=0, cpf=request.player_id)
            return svc.register(user, duration=request.duration or "permanent",
                                reason=request.reason)
        else:
            # GamStop, Spelpaus, ROFUS: self-exclusion via external portal only
            raise NotImplementedError(
                f"API-based registration not supported for {jur}. "
                "Players must self-register via the registry portal."
            )

    def revoke(self, request: RevocationRequest) -> dict:
        """Revoke a self-exclusion (where API supports it)."""
        jur = request.jurisdiction
        if jur == Jurisdiction.BR:
            svc = self._get_brazil()
            user = BrazilUser(id=0, cpf=request.player_id)
            return svc.revoke(user, reason=request.reason)
        else:
            raise NotImplementedError(
                f"API-based revocation not supported for {jur}. "
                "Players must contact the registry directly."
            )

    # ------------------------------------------------------------------
    # Registry-specific check implementations
    # ------------------------------------------------------------------

    def _check_gamstop(self, request: ExclusionCheck) -> ExclusionStatus:
        svc = self._get_gamstop()
        # For single-player check we pass minimal required fields.
        # Full PII must be pre-populated by the caller via player_id lookup.
        # For the API endpoint we accept a JSON body with PII; for simplicity
        # the router uses a minimal stub — the full PII check is in the batch processor.
        user = GamstopUser(
            id=0,
            first_name=request.player_id,  # placeholder — real API uses full PII
            last_name="",
            dob="",
            email=None,
            postcode="",
            mobile=None,
        )
        # NOTE: a real implementation would look up full PII from the player store.
        try:
            is_excluded = svc.check_single(user)
        except Exception as exc:
            # Fail closed: if GAMSTOP is unreachable, treat the player as
            # excluded so access is blocked rather than silently allowed.
            log.error("gamstop: single check failed, failing closed",
                      error=str(exc), player_id=request.player_id)
            is_excluded = True

        return ExclusionStatus(
            player_id=request.player_id,
            registry=Registry.GAMSTOP,
            is_excluded=is_excluded,
            checked_at=datetime.now(timezone.utc),
        )

    def _check_spelpaus(self, request: ExclusionCheck) -> ExclusionStatus:
        svc = self._get_spelpaus()
        from datetime import date
        user = SpelpausUser(id=int(request.player_id or 0),
                            ssn=request.player_id, dob=date.today())
        try:
            is_excluded = svc.check_single(user)
        except Exception as exc:
            # Fail closed: if Spelpaus is unreachable, treat the player as
            # excluded so access is blocked rather than silently allowed.
            log.error("spelpaus: single check failed, failing closed",
                      error=str(exc), player_id=request.player_id)
            is_excluded = True

        return ExclusionStatus(
            player_id=request.player_id,
            registry=Registry.SPELPAUS,
            is_excluded=is_excluded,
            checked_at=datetime.now(timezone.utc),
        )

    def _check_rofus(self, request: ExclusionCheck) -> ExclusionStatus:
        svc = self._get_rofus()
        user = RofusUser(id=0, cpr=request.player_id)
        try:
            is_excluded, until = svc.check_single(user)
        except Exception as exc:
            # Fail closed: if ROFUS is unreachable, treat the player as
            # excluded so access is blocked rather than silently allowed.
            log.error("rofus: single check failed, failing closed",
                      error=str(exc), player_id=request.player_id)
            is_excluded, until = True, None

        return ExclusionStatus(
            player_id=request.player_id,
            registry=Registry.ROFUS,
            is_excluded=is_excluded,
            checked_at=datetime.now(timezone.utc),
            exclusion_period=until,
        )

    def _check_brazil(self, request: ExclusionCheck) -> ExclusionStatus:
        svc = self._get_brazil()
        user = BrazilUser(id=0, cpf=request.player_id)
        try:
            is_excluded, registered_at = svc.check_single(user)
        except Exception as exc:
            # Fail closed: if the Brazil national registry is unreachable,
            # treat the player as excluded so access is blocked rather than
            # silently allowed.
            log.error("brazil: single check failed, failing closed",
                      error=str(exc), player_id=request.player_id)
            is_excluded, registered_at = True, None

        return ExclusionStatus(
            player_id=request.player_id,
            registry=Registry.BRAZIL_NATIONAL,
            is_excluded=is_excluded,
            checked_at=datetime.now(timezone.utc),
            exclusion_period=registered_at,
        )

    # ------------------------------------------------------------------
    # Lazy service construction (env-var based config)
    # ------------------------------------------------------------------

    def _get_gamstop(self) -> GamstopService:
        if not self._gamstop:
            self._gamstop = GamstopService(GamstopApiConfig(
                batch_service_url=os.environ.get("GAMSTOP_API_URL",
                                                  "https://batch.gamstop.io/v2"),
                api_key=os.environ.get("GAMSTOP_API_KEY", ""),
            ))
        return self._gamstop

    def _get_spelpaus(self) -> SpelpausService:
        if not self._spelpaus:
            self._spelpaus = SpelpausService(SpelpausApiConfig(
                batch_service_url=os.environ.get("SPELPAUS_API_URL",
                                                  "https://api.spelpaus.se"),
                api_key=os.environ.get("SPELPAUS_API_KEY", ""),
                actor_id=os.environ.get("SPELPAUS_ACTOR_ID", ""),
            ))
        return self._spelpaus

    def _get_rofus(self) -> RofusService:
        if not self._rofus:
            self._rofus = RofusService(RofusApiConfig(
                base_url=os.environ.get("ROFUS_API_URL",
                                        "https://api.spillemyndigheden.dk"),
                api_key=os.environ.get("ROFUS_API_KEY", ""),
                operator_id=os.environ.get("ROFUS_OPERATOR_ID", ""),
            ))
        return self._rofus

    def _get_brazil(self) -> BrazilRegistryService:
        if not self._brazil:
            self._brazil = BrazilRegistryService(BrazilApiConfig(
                base_url=os.environ.get("BRAZIL_REGISTRY_API_URL",
                                        "https://api.seae.fazenda.gov.br"),
                api_key=os.environ.get("BRAZIL_REGISTRY_API_KEY", ""),
            ))
        return self._brazil
