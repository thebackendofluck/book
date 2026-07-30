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
Mailer service business logic.

Provides:
- TemplateExpander: Jinja2-based template rendering with money filter
- AbstractMailer: base class for email transport backends
- SendGridMailer: HTTP API transport via SendGrid
- SMTPMailer: SMTP transport (courier replacement with smtplib/aiosmtplib)
- MailingSender: orchestrates template lookup, user data, and email dispatch
- BrandSettingsProvider / UserDataProvider / TemplateProvider: data access interfaces

The Scala original used Groovy's GStringTemplateEngine for template expansion.
Python equivalent uses Jinja2 which provides equivalent functionality with
a built-in 'money' filter for currency formatting.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
import jinja2
import structlog

from .models import (
    BrandInfo,
    CurrencyData,
    Email,
    EmailAddress,
    EmailTemplate,
    MailError,
    MailerConfig,
    MailingParams,
    MailResult,
    UserData,
    UserMailingParams,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Template expansion
# ---------------------------------------------------------------------------

class TemplateExpander:
    """
    Jinja2-based template rendering.

    Replaces the Scala Groovy GStringTemplateEngine with Jinja2.
    Provides a 'money' filter that converts minor-unit integer amounts
    to display format using the brand's currency data.

    Template example:
      "Welcome {{ firstName }}! Your deposit of {{ money(amount) }} is confirmed."
    """

    def expand(
        self,
        template_text: str,
        params: dict[str, Any],
        currency_data: CurrencyData,
    ) -> str:
        def money_filter(value: int) -> str:
            display = currency_data.rate * value
            return f"{currency_data.symbol}{display:.2f}"

        env = jinja2.Environment(undefined=jinja2.Undefined)
        env.filters["money"] = money_filter

        # Also make money() available as a function in templates
        full_params: dict[str, Any] = {**params, "money": money_filter}

        try:
            tmpl = env.from_string(template_text)
            return tmpl.render(**full_params)
        except jinja2.TemplateError as exc:
            log.warning("template_expander.render_error", error=str(exc))
            return template_text  # fail open: return unexpanded text

    def expand_for_user(
        self,
        text: str,
        brand: BrandInfo,
        currency_data: CurrencyData,
        user: UserData,
        params: dict[str, Any],
    ) -> str:
        full_params: dict[str, Any] = {
            "brand": brand.data,
            "user": user,
            "brand_name": brand.name,
            "url": brand.url,
            "language": user.language,
            "brand_title": brand.title,
            "first_name": user.data.get("firstName", ""),
            "last_name": user.data.get("lastName", ""),
            "currency_code": currency_data.code,
            "username": user.data.get("username", ""),
            **params,
        }
        return self.expand(text, full_params, currency_data)


# ---------------------------------------------------------------------------
# Data provider interfaces
# ---------------------------------------------------------------------------

class BrandSettingsProvider(ABC):
    @abstractmethod
    def settings_for_brand(self, brand_id: int) -> BrandInfo: ...


class UserDataProvider(ABC):
    @abstractmethod
    async def provide_user_data(self, user_id: int, include_private: bool) -> UserData: ...


class TemplateProvider(ABC):
    @abstractmethod
    async def get_template(
        self,
        template_name: str,
        brand_id: int,
        brand_language: str,
        user_language: str,
        country: str,
    ) -> EmailTemplate | None: ...


class CurrencyDataProvider(ABC):
    @abstractmethod
    async def provide_currency_data(self, currency_code: str) -> CurrencyData: ...


# ---------------------------------------------------------------------------
# Abstract mailer
# ---------------------------------------------------------------------------

class AbstractMailer(ABC):
    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    async def send(self, email: Email) -> MailResult | MailError: ...


# ---------------------------------------------------------------------------
# SendGrid mailer
# ---------------------------------------------------------------------------

class SendGridMailer(AbstractMailer):
    """
    HTTP transport via the SendGrid v3 API.

    Uses httpx for async HTTP. Sends one email per API call.
    Returns MailResult.SENT on HTTP 202 (SendGrid's success status).
    """

    def __init__(self, api_url: str, api_key: str) -> None:
        self._url = api_url
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def description(self) -> str:
        return "SendGrid"

    async def send(self, email: Email) -> MailResult | MailError:
        payload: dict[str, Any] = {
            "personalizations": [{"to": [{"email": email.to}]}],
            "subject": email.subject,
            "from": {"email": email.from_address.address, "name": email.from_address.title},
            "categories": list(email.categories),
            "content": [
                {
                    "type": "text/html" if email.html else "text/plain",
                    "value": email.body,
                }
            ],
            "headers": dict(email.headers),
        }
        try:
            response = await self._client.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            if response.status_code == 202:
                return MailResult.SENT
            log.error("sendgrid.error", status=response.status_code, body=response.text[:200])
            return MailError(result=MailResult.SERVICE_ERROR, error=str(response.status_code))
        except httpx.RequestError as exc:
            log.error("sendgrid.communication_error", error=str(exc))
            return MailError(result=MailResult.COMMUNICATION_ERROR, error=str(exc))

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# SMTP mailer
# ---------------------------------------------------------------------------

class SMTPMailer(AbstractMailer):
    """
    SMTP transport using Python's smtplib.

    Supports optional auth, SSL, and STARTTLS.
    Blocking sends are run in a thread executor to avoid blocking the event loop.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
        use_tls: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._use_tls = use_tls

    @property
    def description(self) -> str:
        return f"SMTP to {self._host}"

    async def send(self, email: Email) -> MailResult | MailError:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_sync, email)
            return MailResult.SENT
        except Exception as exc:  # noqa: BLE001
            log.error("smtp.send_failed", error=str(exc))
            return MailError(result=MailResult.SERVICE_ERROR, error=str(exc))

    def _send_sync(self, email: Email) -> None:
        msg = MIMEMultipart("alternative") if email.html else MIMEText(email.body, "plain")
        if email.html:
            msg.attach(MIMEText(email.body, "html"))
        msg["Subject"] = email.subject
        msg["From"] = f"{email.from_address.title} <{email.from_address.address}>"
        msg["To"] = email.to
        for key, val in email.headers:
            msg[key] = val

        context = ssl.create_default_context() if self._use_ssl or self._use_tls else None
        smtp_cls = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP
        with smtp_cls(self._host, self._port, context=context) as server:
            if self._use_tls and not self._use_ssl:
                server.starttls(context=ssl.create_default_context())
            if self._username and self._password:
                server.login(self._username, self._password)
            server.sendmail(
                email.from_address.address, email.to, msg.as_string()
            )


# ---------------------------------------------------------------------------
# Mailing sender -- orchestrates template + user data + dispatch
# ---------------------------------------------------------------------------

class MailingSender:
    """
    Orchestrates the full email sending pipeline:
      1. Load brand settings
      2. For each user: load user data, currency data, and resolve template
      3. Expand the template with all parameters
      4. Dispatch via the configured mailer backend
    """

    def __init__(
        self,
        brand_settings_provider: BrandSettingsProvider,
        user_data_provider: UserDataProvider,
        template_provider: TemplateProvider,
        currency_data_provider: CurrencyDataProvider,
        mailer: AbstractMailer,
    ) -> None:
        self._brands = brand_settings_provider
        self._users = user_data_provider
        self._templates = template_provider
        self._currencies = currency_data_provider
        self._mailer = mailer
        self._expander = TemplateExpander()

    async def mail(self, params: MailingParams) -> MailResult | MailError:
        brand = self._brands.settings_for_brand(params.brand)
        brand_params: dict[str, Any] = {
            "brand_name": brand.name,
            "url": brand.url,
            "language": brand.language,
            "brand_title": brand.title,
            "support_email": brand.support_email,
            "accounts_email": brand.accounts_email,
        }

        emails_to_send: list[Email] = []

        for user_params in params.users:
            user_data = await self._users.provide_user_data(
                user_params.user_id, user_params.email_override is not None
            )
            currency_data = await self._currencies.provide_currency_data(user_data.currency)
            template = await self._templates.get_template(
                params.template,
                params.brand,
                brand.language,
                user_data.language,
                user_data.country,
            )
            if template is None:
                return MailResult.TEMPLATE_MISSING

            all_params = {**brand_params, **user_params.params, **user_data.data, "currency": currency_data.symbol}
            expanded_body = self._expander.expand(template.content, all_params, currency_data)
            expanded_subject = self._expander.expand(template.subject, all_params, currency_data)

            categories = (
                list(user_params.categories or []) +
                [brand.title, params.template]
            )
            headers = (
                [("X-SOURCE-NAME", params.template)] if user_params.email_override else []
            )

            emails_to_send.append(
                Email(
                    from_address=EmailAddress(address=brand.from_address, title=brand.title),
                    to=user_params.email_override or user_data.email,
                    subject=expanded_subject,
                    body=expanded_body,
                    html=template.html,
                    categories=categories,
                    headers=headers,
                )
            )

        if len(emails_to_send) != len(params.users):
            return MailResult.TEMPLATE_MISSING

        results = await asyncio.gather(*[self._mailer.send(e) for e in emails_to_send])
        for result in results:
            if isinstance(result, MailError):
                return result
        return MailResult.SENT
