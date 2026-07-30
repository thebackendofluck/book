# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""template_registry.py — jurisdiction-aware template lookup with fallback.

Resolves a notification template by (key, jurisdiction, locale), walking a
fallback chain so a missing localized template degrades gracefully instead of
failing the send. Companion module for Chapter 33c.

Lookup order for ("welcome", "BR", "pt-BR"):
    welcome@BR/pt-BR  ->  welcome@BR/*  ->  welcome@*/pt-BR  ->  welcome@*/*
The first template found wins; if none exists, KeyError is raised.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Template:
    key: str
    jurisdiction: str  # ISO country code or "*"
    locale: str  # BCP-47 tag or "*"
    subject: str
    body: str


@dataclass
class TemplateRegistry:
    _templates: dict[tuple[str, str, str], Template] = field(default_factory=dict)

    def register(self, template: Template) -> None:
        self._templates[(template.key, template.jurisdiction, template.locale)] = template

    def resolve(self, key: str, jurisdiction: str, locale: str) -> Template:
        for juris, loc in (
            (jurisdiction, locale),
            (jurisdiction, "*"),
            ("*", locale),
            ("*", "*"),
        ):
            found = self._templates.get((key, juris, loc))
            if found is not None:
                return found
        raise KeyError(f"no template for key={key!r} jurisdiction={jurisdiction!r} locale={locale!r}")

    def render(self, key: str, jurisdiction: str, locale: str, **variables: object) -> tuple[str, str]:
        tmpl = self.resolve(key, jurisdiction, locale)
        return tmpl.subject.format(**variables), tmpl.body.format(**variables)
