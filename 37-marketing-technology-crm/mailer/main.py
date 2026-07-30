# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Mailer service -- FastAPI application.

Exposes a REST endpoint that accepts mailing requests (template + user list)
and dispatches emails via the configured backend (SendGrid or SMTP).

Endpoints:
  POST /mail          -- send templated emails to one or more users
  GET  /healthz       -- health check

Configuration is via environment variables:
  MAILER_TYPE           sendgrid | smtp
  SENDGRID_URL          SendGrid API URL
  SENDGRID_API_KEY      SendGrid API key
  SMTP_HOST             SMTP server hostname
  SMTP_PORT             SMTP port
  SMTP_USERNAME         SMTP auth username (optional)
  SMTP_PASSWORD         SMTP auth password (optional)
  SMTP_USE_SSL          true/false
  SMTP_USE_TLS          true/false
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .models import (
    BrandInfo,
    CurrencyData,
    Email,
    EmailTemplate,
    MailerConfig,
    MailingParams,
    MailResult,
    UserData,
)
from .service import (
    AbstractMailer,
    BrandSettingsProvider,
    CurrencyDataProvider,
    MailingSender,
    SendGridMailer,
    SMTPMailer,
    TemplateProvider,
    UserDataProvider,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Stub data providers (swap with real DB-backed implementations)
# ---------------------------------------------------------------------------

class StubBrandSettings(BrandSettingsProvider):
    def settings_for_brand(self, brand_id: int) -> BrandInfo:
        return BrandInfo(
            id=brand_id, name=f"Brand {brand_id}", url=f"https://brand{brand_id}.com",
            language="en", title=f"Brand {brand_id}", support_email=f"support@brand{brand_id}.com",
            accounts_email=f"accounts@brand{brand_id}.com", from_address=f"noreply@brand{brand_id}.com",
        )


class StubUserData(UserDataProvider):
    async def provide_user_data(self, user_id: int, include_private: bool) -> UserData:
        return UserData(user_id=user_id, email=f"user{user_id}@example.com", language="en", country="GB", currency="GBP")


class StubTemplateProvider(TemplateProvider):
    async def get_template(self, template_name: str, brand_id: int, brand_language: str, user_language: str, country: str) -> EmailTemplate | None:
        return EmailTemplate(name=template_name, brand_id=brand_id, language="en", subject="Test", content="Hello {{ first_name }}", html=False)


class StubCurrencyData(CurrencyDataProvider):
    async def provide_currency_data(self, currency_code: str) -> CurrencyData:
        symbols = {"GBP": "£", "USD": "$", "EUR": "€"}
        return CurrencyData(code=currency_code, symbol=symbols.get(currency_code, currency_code), rate=0.01)


def build_mailer(config: MailerConfig) -> AbstractMailer:
    if config.mailer_type == "sendgrid":
        return SendGridMailer(config.sendgrid_url, config.sendgrid_api_key)
    elif config.mailer_type == "smtp":
        return SMTPMailer(
            config.smtp_host,
            config.smtp_port,
            config.smtp_username,
            config.smtp_password,
            config.smtp_use_ssl,
            config.smtp_use_tls,
        )
    else:
        raise ValueError(f"Unknown mailer type: {config.mailer_type}")


def load_config() -> MailerConfig:
    return MailerConfig(
        mailer_type=os.getenv("MAILER_TYPE", "sendgrid"),
        sendgrid_url=os.getenv("SENDGRID_URL", "https://api.sendgrid.com/v3/mail/send"),
        sendgrid_api_key=os.getenv("SENDGRID_API_KEY", ""),
        smtp_host=os.getenv("SMTP_HOST", "localhost"),
        smtp_port=int(os.getenv("SMTP_PORT", "25")),
        smtp_username=os.getenv("SMTP_USERNAME"),
        smtp_password=os.getenv("SMTP_PASSWORD"),
        smtp_use_ssl=os.getenv("SMTP_USE_SSL", "false").lower() == "true",
        smtp_use_tls=os.getenv("SMTP_USE_TLS", "false").lower() == "true",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8888")),
    )


_config = load_config()
_mailer = build_mailer(_config)
_sender = MailingSender(
    brand_settings_provider=StubBrandSettings(),
    user_data_provider=StubUserData(),
    template_provider=StubTemplateProvider(),
    currency_data_provider=StubCurrencyData(),
    mailer=_mailer,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("mailer.starting", backend=_config.mailer_type)
    yield
    if hasattr(_mailer, "aclose"):
        await _mailer.aclose()  # type: ignore[attr-defined]
    log.info("mailer.stopped")


app = FastAPI(title="Mailer Service", lifespan=lifespan)


@app.post("/mail")
async def send_mail(body: MailingParams) -> JSONResponse:
    """
    Send templated emails to the specified users.

    Returns 200 with result=sent on success, or an error description on failure.
    """
    result = await _sender.mail(body)
    if isinstance(result, str):  # MailResult enum
        if result == MailResult.TEMPLATE_MISSING:
            raise HTTPException(status_code=404, detail="Template not found")
        return JSONResponse({"result": result})
    else:
        raise HTTPException(status_code=502, detail=result.error or "Mail service error")


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}
