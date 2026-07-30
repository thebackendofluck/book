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
AML/Fraud Detection Service — COAF SAR Reporter
================================================
Generates and submits Suspicious Activity Reports (SARs) to COAF.

Legal basis:
  - Lei 9.613/1998 (Lei de Lavagem de Dinheiro)
  - Resolução BCB 44/2020 — obligations for payment institutions
  - COAF Instrução Normativa 01/2017 — reporting format and deadlines

Submission deadlines:
  - HIGH urgency: within 24 hours
  - NORMAL urgency: within 1 business day

Reports are stored in-memory (replace with DB persistence in production).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from models import COAFReport, COAFReportRequest, ReportStatus, ReportUrgency

log = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

COAF_API_URL: str = os.getenv("COAF_API_URL", "https://api.coaf.fazenda.gov.br")
COAF_API_KEY: str = os.getenv("COAF_API_KEY", "changeme")


class COAFReporter:
    """
    Generates Lei 9.613/1998-compliant SARs and submits them to the COAF API.
    """

    def __init__(
        self,
        api_url: str = COAF_API_URL,
        api_key: str = COAF_API_KEY,
    ) -> None:
        self._api_url = api_url
        self._api_key = api_key
        # In-memory store; swap for DB-backed store in production
        self._reports: dict[str, COAFReport] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate_report(self, req: COAFReportRequest) -> COAFReport:
        """
        Create a COAF SAR from the request.
        Urgent reports are submitted immediately; normal reports are queued.
        """
        report_id = f"COAF-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()

        report = COAFReport(
            report_id=report_id,
            cpf=req.cpf,
            report_reason=req.report_reason,
            transactions=req.transactions,
            evidence_summary=self._build_evidence_text(req),
            status=ReportStatus.PENDING,
            submitted_at=now,
            coaf_protocol=None,
        )

        self._reports[report_id] = report

        log.info(
            "coaf_reporter.report_created",
            report_id=report_id,
            cpf=req.cpf,
            reason=req.report_reason,
            urgency=req.urgency,
            tx_count=len(req.transactions),
        )

        # Attempt immediate submission for HIGH urgency (Lei 9.613, art. 11)
        if req.urgency == ReportUrgency.HIGH:
            try:
                submitted = await self._submit_report(report)
                self._reports[report_id] = submitted
                return submitted
            except Exception as exc:
                log.warning(
                    "coaf_reporter.urgent_submission_failed",
                    report_id=report_id,
                    error=str(exc),
                )

        return report

    async def submit_report(self, report_id: str) -> COAFReport:
        """Manually trigger submission of a pending report."""
        report = self._reports.get(report_id)
        if report is None:
            raise KeyError(f"Report {report_id} not found")
        submitted = await self._submit_report(report)
        self._reports[report_id] = submitted
        return submitted

    async def generate_batch(
        self, requests: list[COAFReportRequest]
    ) -> list[COAFReport]:
        """Generate multiple SARs in one call (batch reporting)."""
        reports = []
        for req in requests:
            report = await self.generate_report(req)
            reports.append(report)
        log.info("coaf_reporter.batch_generated", count=len(reports))
        return reports

    def get_report(self, report_id: str) -> Optional[COAFReport]:
        return self._reports.get(report_id)

    def get_pending_reports(self) -> list[COAFReport]:
        return [r for r in self._reports.values() if r.status == ReportStatus.PENDING]

    def get_all_reports(self) -> list[COAFReport]:
        return list(self._reports.values())

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _submit_report(self, report: COAFReport) -> COAFReport:
        payload = self._build_coaf_payload(report)
        headers = {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._api_url}/v1/reports",
                content=json.dumps(payload),
                headers=headers,
            )

        if response.status_code in (200, 201):
            protocol = self._extract_protocol(response.text)
            submitted = COAFReport(
                **{
                    **report.model_dump(),
                    "status": ReportStatus.SUBMITTED,
                    "coaf_protocol": protocol,
                }
            )
            log.info(
                "coaf_reporter.submitted",
                report_id=report.report_id,
                protocol=protocol,
            )
            return submitted
        else:
            log.error(
                "coaf_reporter.submission_error",
                report_id=report.report_id,
                http_status=response.status_code,
                body=response.text[:200],
            )
            raise RuntimeError(
                f"COAF API returned {response.status_code}: {response.text[:200]}"
            )

    def _build_evidence_text(self, req: COAFReportRequest) -> str:
        """
        Build the formal SAR evidence text following COAF IN-01/2017 structure.
        """
        tx_ids = ", ".join(req.transactions)
        now = datetime.now(timezone.utc).isoformat()
        return (
            "RELATÓRIO DE ATIVIDADE SUSPEITA — COAF\n\n"
            f"CPF REPORTADO: {req.cpf}\n"
            f"MOTIVO: {req.report_reason}\n"
            f"TRANSAÇÕES RELACIONADAS: {len(req.transactions)}\n"
            f"IDs: {tx_ids}\n\n"
            "RESUMO DA EVIDÊNCIA:\n"
            f"{req.evidence_summary}\n\n"
            f"Gerado em: {now}\n"
            f"Urgência: {req.urgency.value}\n\n"
            "Este relatório foi gerado automaticamente pelo sistema de detecção AML.\n"
            "Revisão manual recomendada antes da submissão final."
        )

    def _build_coaf_payload(self, report: COAFReport) -> dict:
        """
        Build the structured JSON payload per COAF IN-01/2017 specification.
        """
        return {
            "identificacao": {
                "protocolo_interno": report.report_id,
                "data_ocorrencia": report.submitted_at,
                "tipo_operacao": report.report_reason,
            },
            "pessoa": {
                "cpf": report.cpf,
                "tipo": "PF",
            },
            "operacoes": report.transactions,
            "descricao": report.evidence_summary.replace("\n", " "),
            "urgencia": "NORMAL",
        }

    @staticmethod
    def _extract_protocol(response_body: str) -> str:
        """Extract the COAF protocol number from the API response."""
        try:
            data = json.loads(response_body)
            return str(data.get("protocol", uuid.uuid4().hex))
        except (json.JSONDecodeError, AttributeError):
            return uuid.uuid4().hex
