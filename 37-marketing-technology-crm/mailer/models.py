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
Domain models for the email sending service.

The mailer supports two transport backends:
  - SendGrid (HTTP API): used for transactional email at scale
  - SMTP: used for internal notifications or environments without SendGrid

Template expansion uses Jinja2 (replacing the Groovy GStringTemplateEngine
from the Scala original) with a custom 'money' filter for currency formatting.

Amount formatting: currency amounts are stored in minor units (cents/pence);
the 'money' template filter converts to the display format using the brand's
currency rate and symbol.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EmailAddress(BaseModel):
    address: str
    title: str


class Email(BaseModel):
    from_address: EmailAddress
    to: str
    html: bool = False
    body: str = ""
    subject: str = ""
    headers: list[tuple[str, str]] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class MailResult(str, Enum):
    SENT = "sent"
    TEMPLATE_MISSING = "template_missing"
    SERVICE_ERROR = "service_error"
    COMMUNICATION_ERROR = "communication_error"


class MailError(BaseModel):
    result: MailResult
    error: str | None = None


class BrandInfo(BaseModel):
    id: int
    name: str
    url: str
    language: str
    title: str
    support_email: str
    accounts_email: str
    from_address: str
    data: dict[str, Any] = Field(default_factory=dict)


class UserData(BaseModel):
    user_id: int
    email: str
    language: str
    country: str
    currency: str
    data: dict[str, Any] = Field(default_factory=dict)


class CurrencyData(BaseModel):
    code: str
    symbol: str
    rate: float = 1.0  # conversion rate for display (minor -> display unit)


class EmailTemplate(BaseModel):
    name: str
    brand_id: int
    language: str
    subject: str
    content: str
    html: bool = True


class UserMailingParams(BaseModel):
    user_id: int
    params: dict[str, Any] = Field(default_factory=dict)
    email_override: str | None = None
    categories: list[str] | None = None


class MailingParams(BaseModel):
    brand: int
    template: str
    users: list[UserMailingParams]


class MailerConfig(BaseModel):
    mailer_type: str = "sendgrid"
    sendgrid_url: str = "https://api.sendgrid.com/v3/mail/send"
    sendgrid_api_key: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_ssl: bool = False
    smtp_use_tls: bool = False
    host: str = "0.0.0.0"
    port: int = 8888
