#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Website Jurisdiction Compliance Checker
Verifies that a casino website meets regulatory requirements for its target jurisdiction.

Usage:
    python compliance_checker.py --url https://new.acmetocasino.com/lobby-uk.html --jurisdiction uk
    python compliance_checker.py --url https://new.acmetocasino.com/lobby-brazil.html --jurisdiction brazil
    python compliance_checker.py --check-all  # checks all 5 lobbies
"""

import argparse
import html as html_module
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Check definitions
# ---------------------------------------------------------------------------

@dataclass
class Check:
    """A single compliance check: search for any of the patterns in the HTML."""
    name: str
    patterns: list[str]          # case-insensitive substring patterns; any match = pass
    required: bool = True        # if False, failure is a warning only
    note: str = ""               # extra context for the report


@dataclass
class JurisdictionSpec:
    name: str
    url: str
    checks: list[Check] = field(default_factory=list)


def _checks_uk() -> list[Check]:
    return [
        Check("GamStop link",
              ["gamstop.co.uk", "gamstop"],
              note="UKGC mandatory self-exclusion scheme"),
        Check("BeGambleAware link",
              ["begambleaware.org", "begambleaware"],
              note="UKGC mandatory safer-gambling signpost"),
        Check("GamCare link",
              ["gamcare.org.uk", "gamcare"],
              note="UKGC mandatory counselling service link"),
        Check("License number displayed",
              [r"license\s*no\.?\s*\d{4,6}", r"licence\s*no\.?\s*\d{4,6}",
               "license number", "licence number", "52894"],
              note="UKGC operating licence number must appear on site"),
        Check("18+ age badge",
              ["18+", "18 or older", "18 years or older", "18 years or above"],
              note="Mandatory age restriction indicator"),
        Check("Session timer / reality check",
              ["session timer", "reality check", "session timer active",
               "session limit", "mandatory pause"],
              note="UKGC requires reality-check pop-up or session timer"),
        Check("Deposit limit tool reference",
              ["deposit limit", "deposit limits", "ins\u00e4ttningsgr\u00e4ns"],
              note="UKGC requires accessible deposit-limit controls"),
        Check("Cookie consent mechanism",
              ["cookie", "cookies", "pecr", "gdpr"],
              note="UK PECR requires cookie consent"),
        Check("Terms & conditions link",
              ["terms", "terms and conditions", "t&amp;c", "t&c"],
              note="Basic consumer-law requirement"),
    ]


def _checks_malta() -> list[Check]:
    return [
        Check("MGA license number",
              [r"mga/b2c/\d+/\d{4}", "mga/b2c/123/2024", "mga/b2c"],
              note="MGA licence number format: MGA/B2C/XXX/XXXX"),
        Check("RGF Malta reference",
              ["rgfmalta", "responsible gaming foundation malta",
               "responsible gaming foundation"],
              note="Maltese law requires RGF Malta link"),
        Check("18+ badge",
              ["18+", "18 or older", "18 years"],
              note="MGA mandatory age restriction"),
        Check("Self-exclusion tool link",
              ["self-exclu", "self exclu", "selfexclu",
               "mga.org.mt/player-support/self-exclusion",
               "mga self-exclusion"],
              note="MGA Player Protection Directive requires self-exclusion link"),
        Check("Player Protection Directive reference",
              ["player protection directive", "player protection",
               "eu player protection"],
              note="MGA mandatory reference to the Player Protection Directive"),
        Check("Cookie consent",
              ["cookie", "gdpr"],
              note="EU GDPR / ePrivacy cookie consent"),
    ]


def _checks_sweden() -> list[Check]:
    return [
        Check("Spelpaus link",
              ["spelpaus.se", "spelpaus"],
              note="Swedish law requires link to national self-exclusion register"),
        Check("Stodlinjen reference",
              ["stodlinjen.se", "stodlinjen", "st\u00f6dlinjen"],
              note="Swedish helpline (Stodlinjen) must be referenced"),
        Check("Spelinspektionen license reference",
              ["spelinspektionen", "spelinspektionen licensierat",
               "licensierat av spelinspektionen"],
              note="Swedish regulator must be named on site"),
        Check("Single bonus restriction notice",
              ["ett v\u00e4lkomstbonus per spelare",
               "v\u00e4lkomstbonusar begr\u00e4nsas",
               "welcome bonus limited to one",
               "single bonus", "one bonus per player",
               "ett per spelare"],
              note="Sweden restricts welcome bonuses to one per player (personnummer)"),
        Check("Mandatory deposit limits",
              ["ins\u00e4ttningsgr\u00e4ns", "obligatoriska ins\u00e4ttningsgr\u00e4nser",
               "deposit limit", "mandatory deposit limit"],
              note="Swedish law requires deposit limits before first deposit"),
        Check("18+ badge",
              ["18+", "18 \u00e5r", "jag \u00e4r 18"],
              note="Mandatory age restriction"),
    ]


def _checks_brazil() -> list[Check]:
    return [
        Check("SPA-MF reference / Lei 14.790",
              ["spa-mf", "secretaria de pr\u00eamios e apostas",
               "lei 14.790", "lei n\u00ba 14.790", "lei no 14.790"],
              note="Brazil's gambling law and regulator must be named"),
        Check("Disque 100 helpline",
              ["disque 100", "disque100"],
              note="Mandatory welfare helpline (Disque Direitos Humanos)"),
        Check("Ligue 180 helpline",
              ["ligue 180", "ligue180"],
              note="Mandatory violence/addiction helpline for women"),
        Check("jogadorresponsavel.com.br link",
              ["jogadorresponsavel.com.br", "jogadorresponsavel"],
              note="Mandatory responsible-gambling portal mandated by SPA-MF"),
        Check("18+ / Proibido para menores",
              ["proibido para menores", "18+", "18 anos",
               "tenho 18 anos"],
              note="Brazilian law prohibits access by under-18s; must be stated"),
        Check("PIX-only payment notice",
              ["somente pix", "exclusivamente via pix", "pix only",
               "apenas pix", "cart\u00e3o de cr\u00e9dito proibido"],
              note="Brazil prohibits credit-card deposits; only PIX allowed"),
        Check("CPF required notice",
              ["cpf", "cpf obrigat\u00f3rio", "cpf v\u00e1lido",
               "cpf v\u00e1lido obrigat\u00f3rio"],
              note="Brazilian KYC requires valid CPF for all players"),
        Check("SIGAP badge",
              ["sigap", "sistema de gest\u00e3o de apostas"],
              note="Real-time reporting to SIGAP is mandatory"),
        Check(".bet.br domain reference",
              [".bet.br", "dom\u00ednio oficial .bet.br",
               "dom\u00ednio .bet.br"],
              note="Licensed operators must use .bet.br TLD"),
        Check("Welfare block notice (Bolsa Familia)",
              ["bolsa fam\u00edlia", "bpc", "benefici\u00e1rios de bolsa",
               "bolsa familia"],
              note="Players receiving social welfare may not gamble under Lei 14.790"),
    ]


def _checks_denmark() -> list[Check]:
    return [
        Check("ROFUS link",
              ["rofus.nu", "rofus"],
              note="ROFUS is the mandatory Danish self-exclusion register"),
        Check("Spillemyndigheden reference",
              ["spillemyndigheden"],
              note="Danish regulator must be named and linked"),
        Check("18+ badge",
              ["18+", "18 \u00e5r", "jeg er 18"],
              note="Mandatory age restriction"),
        Check("Cookie consent",
              ["cookie", "gdpr", "cookieloven"],
              note="Danish cookieloven + GDPR require consent mechanism"),
    ]


# ---------------------------------------------------------------------------
# Jurisdiction registry
# ---------------------------------------------------------------------------

JURISDICTIONS: dict[str, JurisdictionSpec] = {
    "uk": JurisdictionSpec(
        name="United Kingdom (UKGC)",
        url="https://new.acmetocasino.com/lobby-uk.html",
        checks=_checks_uk(),
    ),
    "malta": JurisdictionSpec(
        name="Malta (MGA)",
        url="https://new.acmetocasino.com/lobby-malta.html",
        checks=_checks_malta(),
    ),
    "sweden": JurisdictionSpec(
        name="Sweden (Spelinspektionen)",
        url="https://new.acmetocasino.com/lobby-sweden.html",
        checks=_checks_sweden(),
    ),
    "brazil": JurisdictionSpec(
        name="Brazil (SPA-MF / Lei 14.790)",
        url="https://new.acmetocasino.com/lobby-brazil.html",
        checks=_checks_brazil(),
    ),
    "denmark": JurisdictionSpec(
        name="Denmark (Spillemyndigheden)",
        url="https://new.acmetocasino.com/lobby-denmark.html",
        checks=_checks_denmark(),
    ),
}


# ---------------------------------------------------------------------------
# Fetch & check logic
# ---------------------------------------------------------------------------

def fetch_html(url: str, timeout: float = 15.0) -> str:
    """Fetch URL, decode HTML entities, and return full text for pattern matching.

    Decoding HTML entities (e.g. &#228; -> ä) allows patterns to match
    localised text that browsers render correctly but appears encoded in
    raw HTTP responses.
    """
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "ComplianceChecker/1.0"})
        resp.raise_for_status()
        return html_module.unescape(resp.text)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"HTTP {exc.response.status_code} fetching {url}") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Request error fetching {url}: {exc}") from exc


def run_check(check: Check, html_lower: str) -> bool:
    """Return True if any pattern matches the (lowercased) HTML."""
    for pattern in check.patterns:
        # Try regex first; fall back to literal substring
        try:
            if re.search(pattern, html_lower, re.IGNORECASE):
                return True
        except re.error:
            if pattern.lower() in html_lower:
                return True
    return False


@dataclass
class CheckResult:
    check: Check
    passed: bool


@dataclass
class JurisdictionReport:
    spec: JurisdictionSpec
    url: str
    fetch_error: Optional[str]
    results: list[CheckResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0.0

    @property
    def overall_pass(self) -> bool:
        """Overall pass only if ALL required checks pass."""
        if self.fetch_error:
            return False
        return all(r.passed for r in self.results if r.check.required)


def check_jurisdiction(spec: JurisdictionSpec, url: Optional[str] = None) -> JurisdictionReport:
    target_url = url or spec.url
    report = JurisdictionReport(spec=spec, url=target_url, fetch_error=None)

    try:
        html = fetch_html(target_url)
    except RuntimeError as exc:
        report.fetch_error = str(exc)
        return report

    html_lower = html.lower()

    for check in spec.checks:
        passed = run_check(check, html_lower)
        report.results.append(CheckResult(check=check, passed=passed))

    return report


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"
LINE = "-" * 72


def format_report(report: JurisdictionReport, use_color: bool = True) -> str:
    def _p(text: str) -> str:
        return text if use_color else re.sub(r"\033\[[0-9;]+m", "", text)

    lines: list[str] = []
    lines.append(LINE)
    lines.append(_p(f"{BOLD}Jurisdiction : {report.spec.name}{RESET}"))
    lines.append(f"URL          : {report.url}")

    if report.fetch_error:
        lines.append(_p(f"Fetch status : {FAIL} — {report.fetch_error}"))
        lines.append(LINE)
        return "\n".join(lines)

    lines.append(f"Fetch status : OK")
    lines.append("")

    # Column header
    lines.append(f"  {'Status':<8}  {'Check'}")
    lines.append(f"  {'-'*6}  {'-'*50}")

    for r in report.results:
        if r.passed:
            status = _p(f"{PASS}    ")
        elif not r.check.required:
            status = _p(f"{WARN}    ")
        else:
            status = _p(f"{FAIL}    ")

        note_str = f"  # {r.check.note}" if r.check.note else ""
        lines.append(f"  {status}  {r.check.name}{note_str}")

    lines.append("")
    overall = _p(f"{PASS}") if report.overall_pass else _p(f"{FAIL}")
    lines.append(_p(
        f"  Result : {overall}  —  "
        f"{BOLD}{report.passed}/{report.total}{RESET} checks passed "
        f"({report.pass_rate:.0f}%)"
    ))
    lines.append(LINE)
    return "\n".join(lines)


def print_summary(reports: list[JurisdictionReport], use_color: bool = True) -> None:
    def _p(text: str) -> str:
        return text if use_color else re.sub(r"\033\[[0-9;]+m", "", text)

    print("\n" + "=" * 72)
    print(_p(f"{BOLD}COMPLIANCE SUMMARY{RESET}"))
    print("=" * 72)
    col = f"  {'Jurisdiction':<38} {'Result':<10} {'Score'}"
    print(col)
    print("  " + "-" * 60)
    all_pass = True
    for r in reports:
        if r.fetch_error:
            status = _p(f"{FAIL} (fetch error)")
            all_pass = False
        elif r.overall_pass:
            status = _p(f"{PASS}          ")
        else:
            status = _p(f"{FAIL}          ")
            all_pass = False
        score = f"{r.passed}/{r.total} ({r.pass_rate:.0f}%)" if not r.fetch_error else "N/A"
        print(f"  {r.spec.name:<38} {status} {score}")
    print("=" * 72)
    overall = _p(f"{BOLD}{PASS} — All jurisdictions compliant{RESET}") \
        if all_pass \
        else _p(f"{BOLD}{FAIL} — One or more jurisdictions have failures{RESET}")
    print(f"  Overall: {overall}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Casino Website Jurisdiction Compliance Checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--url",
        help="URL of the lobby page to check (requires --jurisdiction)",
    )
    group.add_argument(
        "--check-all",
        action="store_true",
        help="Check all 5 jurisdiction lobbies",
    )
    parser.add_argument(
        "--jurisdiction",
        choices=list(JURISDICTIONS.keys()),
        help="Jurisdiction to check when --url is used",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour codes in output",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    use_color = not args.no_color

    if args.check_all:
        reports: list[JurisdictionReport] = []
        for key, spec in JURISDICTIONS.items():
            print(f"\nChecking {spec.name} ({spec.url}) ...")
            report = check_jurisdiction(spec)
            print(format_report(report, use_color=use_color))
            reports.append(report)
        print_summary(reports, use_color=use_color)
        return 0 if all(r.overall_pass for r in reports) else 1

    else:
        if not args.jurisdiction:
            parser.error("--jurisdiction is required when using --url")
        spec = JURISDICTIONS[args.jurisdiction]
        report = check_jurisdiction(spec, url=args.url)
        print(format_report(report, use_color=use_color))
        return 0 if report.overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
