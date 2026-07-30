<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 06: Licensing Guide

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 06 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Tooling for jurisdiction selection, license-application document generation, ongoing compliance monitoring, and renewal tracking across 20+ regulatory bodies.

## Overview

Chapter 6 maps the real timelines, costs, and technical requirements for gambling licenses from New Jersey to Malta to Brazil. These scripts automate the decision and compliance workflow: score jurisdictions against operator criteria, generate application-ready document checklists, run compliance rule checks for a live license, coordinate testing-lab submissions, and track renewal deadlines before they become emergencies.

## Contents

- `jurisdiction-compliance/`
  - `compliance_checker.py` — Validates operator configuration against jurisdiction-specific rules (deposit limits, RTP floors, self-exclusion hooks, etc.)
  - `jurisdiction_matrix_test.py` — Pytest suite asserting the compliance matrix stays correct as rules are updated
- `implementation/selection/`
  - `jurisdiction_selector.py` — Scores jurisdictions against configurable operator criteria (market access, cost, timeline, cloud allowed)
- `implementation/documentation/`
  - `license_doc_generator.py` — Generates per-jurisdiction application checklists and document templates
- `implementation/compliance/`
  - `ongoing_compliance.py` — Monitors live license obligations (reporting deadlines, player-protection checks, fee payments)
- `implementation/tracking/`
  - `license_renewal_tracker.py` — Tracks renewal windows, notifies teams of approaching deadlines
- `implementation/testing-lab/`
  - `lab_coordinator.py` — Coordinates RNG and game-math submissions to approved testing labs (BMM, GLI, eCOGRA)
- `game-info-bar.js` — Front-end snippet rendering the regulatory info bar (license badge, RTP, jurisdiction) required in several EU markets
- `compliance-research.md` — Research notes on jurisdiction-specific edge cases
- `gli-16/` — GLI-16 v3.0 (2024) Cashless Gaming Systems
  - `wallet-reconciliation-check.py` — Nightly reconciliation between provider settlement export and operator wallet ledger; detects orphan credits/debits, amount mismatches, duplicate `txid`s. Emits a JSON report retained per SPA/BACEN 5-year rule.

## Technology Stack

- **Backend scripts:** Python 3.11+
- **Front-end snippet:** JavaScript (ES2020, no build step)
- **Testing:** pytest

## Prerequisites

- Python 3.11 or later
- pytest (for `jurisdiction_matrix_test.py`): `pip install pytest`

## How to Run

```bash
cd scripts/chapter-06
# Run jurisdiction selection
python -m implementation.selection.jurisdiction_selector

# Run compliance checker
python -m jurisdiction-compliance.compliance_checker

# Run the test matrix
pytest jurisdiction-compliance/jurisdiction_matrix_test.py -v
```

## Related

- See Chapter 6 in the book for the full licensing guide including cost breakdowns, timeline data, and technical requirements per jurisdiction.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 1 · last updated 2026-04-16.</sub>
