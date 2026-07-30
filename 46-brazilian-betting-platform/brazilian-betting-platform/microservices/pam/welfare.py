# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PAM Service — SIGAP Impediments Client
======================================
Checks whether a player is barred from betting through the official SIGAP
Impediments API. Despite the legacy module/class names, this code does not
query CadÚnico, MDS, SENARC, banks, or the origin of a PIX transfer.

For Bolsa Família and BPC, the operator learns only that the CPF is returned
as ``IMPEDIDO`` with reason ``PROGRAMA_SOCIAL``. The same API can return other
current regulatory reasons, including centralized self-exclusion and the
credit-program impediments added in 2026.

Required controls:
  - send the normalized 11-digit CPF, not a hash, to the official API;
  - consult at onboarding, first login of the day, and at least every 15 days;
  - never release betting when a mandatory check is unavailable;
  - retain only the minimum audit evidence (result, reason, request id, time);
  - obtain the JWT through the official e-CNPJ authentication flow.

Official documentation:
  https://documentacao-sigap-rec.ni.estaleiro.serpro.gov.br/documentacao_api_impedidos/
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

PRODUCTION_URL = (
    "https://sigap-impedidos.fazenda.gov.br"
    "/impedimento/v2/condicao/{cpf}"
)

# CPFs explicitly published for the SIGAP homologation environment.
_HOMOLOGATION_RESULTS: dict[str, tuple[str, ...]] = {
    "28784142090": ("PROGRAMA_SOCIAL",),
    "08782758000": ("PROGRAMA_SOCIAL",),
    "08940473965": ("PROGRAMA_SOCIAL",),
    "51077358008": ("AUTOEXCLUSAO_CENTRALIZADA",),
    "62564939074": ("AUTOEXCLUSAO_CENTRALIZADA",),
    "15690288691": ("AUTOEXCLUSAO_CENTRALIZADA",),
    "10996230572": ("AUTOEXCLUSAO_CENTRALIZADA", "PROGRAMA_SOCIAL"),
    "83941151878": ("PROGRAMA_NOVO_DESENROLA_BRASIL",),
    "15959816679": ("RENEGOCIACAO_FIES",),
    "55851894091": ("PROGRAMA_DESENROLA_ADIMPLENTES",),
    "99458738067": ("PROGRAMA_FIES_EMPREENDEDOR",),
}


class WelfareBeneficiaryError(ValueError):
    """Legacy name: the player has at least one SIGAP impediment."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__(f"SIGAP betting impediment: {', '.join(reasons)}")


class WelfareCheckError(RuntimeError):
    """The mandatory SIGAP query could not be completed safely."""


@dataclass(frozen=True)
class WelfareCheckResult:
    """Minimal, privacy-conscious result from SIGAP Impediments API v2."""

    cpf_hash: str
    resultado: str
    motivos: tuple[str, ...]
    request_id: str
    checked_at: datetime
    source: str = "SIGAP Impediments API v2"

    @property
    def restriction_active(self) -> bool:
        return self.resultado == "IMPEDIDO"

    @property
    def social_program_restriction(self) -> bool:
        return "PROGRAMA_SOCIAL" in self.motivos


class WelfareRegistryClient:
    """
    Async SIGAP client.

    ``mock=True`` reproduces the fictitious CPFs published in the official
    homologation documentation. Production must supply a current JWT obtained
    through the e-CNPJ authentication flow.
    """

    def __init__(
        self,
        *,
        access_token: Optional[str] = None,
        base_url: str = PRODUCTION_URL,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 5.0,
        mock: bool = False,
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds
        self.mock = mock

    @staticmethod
    def _normalise_cpf(cpf: str) -> str:
        normalized = re.sub(r"\D", "", cpf)
        if len(normalized) != 11:
            raise ValueError("CPF must contain exactly 11 digits")
        return normalized

    @staticmethod
    def _cpf_hash(cpf: str) -> str:
        return hashlib.sha256(cpf.encode("utf-8")).hexdigest()

    async def check(self, cpf: str) -> WelfareCheckResult:
        """Query SIGAP using the raw normalized CPF; never log or return it."""
        normalized = self._normalise_cpf(cpf)
        cpf_hash = self._cpf_hash(normalized)

        if self.mock:
            motivos = _HOMOLOGATION_RESULTS.get(normalized, ())
            result = WelfareCheckResult(
                cpf_hash=cpf_hash,
                resultado="IMPEDIDO" if motivos else "NAO_IMPEDIDO",
                motivos=motivos,
                request_id=f"mock-{uuid.uuid4()}",
                checked_at=datetime.now(timezone.utc),
                source="SIGAP homologation fixtures",
            )
        else:
            result = await self._query_sigap(normalized, cpf_hash)

        logger.info(
            "sigap_impediment_check",
            cpf_hash_prefix=cpf_hash[:8],
            resultado=result.resultado,
            motivos=result.motivos,
            request_id=result.request_id,
        )
        return result

    async def _query_sigap(self, cpf: str, cpf_hash: str) -> WelfareCheckResult:
        if not self.access_token:
            raise WelfareCheckError(
                "SIGAP access token is not configured; betting must remain blocked"
            )

        headers = {"Authorization": f"Bearer {self.access_token}"}
        owns_client = self.http_client is None
        client = self.http_client or httpx.AsyncClient(timeout=self.timeout_seconds)

        try:
            response = await client.get(self.base_url.format(cpf=cpf), headers=headers)
            response.raise_for_status()
            payload = response.json()

            resultado = payload["resultado"]
            motivos = tuple(payload.get("motivos") or ())
            request_id = payload["idRequisicao"]
            if resultado not in {"IMPEDIDO", "NAO_IMPEDIDO"}:
                raise ValueError(f"unexpected SIGAP result: {resultado!r}")
            if resultado == "IMPEDIDO" and not motivos:
                raise ValueError("SIGAP returned IMPEDIDO without a reason")

            return WelfareCheckResult(
                cpf_hash=cpf_hash,
                resultado=resultado,
                motivos=motivos,
                request_id=request_id,
                checked_at=datetime.now(timezone.utc),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.error(
                "sigap_impediment_check_failed",
                cpf_hash_prefix=cpf_hash[:8],
                error_type=type(exc).__name__,
            )
            raise WelfareCheckError(
                "SIGAP impediment check unavailable; betting must remain blocked"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def check_or_raise(self, cpf: str) -> WelfareCheckResult:
        """Run the mandatory check and raise for any returned impediment."""
        result = await self.check(cpf)
        if result.restriction_active:
            raise WelfareBeneficiaryError(result.motivos)
        return result
