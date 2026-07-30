# Chapter 26b — Self-Exclusion Registries Implementation Scripts

Companion scripts for **Chapter 26b: Self-Exclusion Registries — From API to Audit Trail**.

## Scripts

| Script | Lines | Description |
|---|---|---|
| `us_multi_state_exclusion_check.py` | ~240 | Multi-state US self-exclusion service. Abstract provider protocol with NJ DGE and PA PGCB adapters. Fuzzy matching engine for name/DOB/postcode. |
| `gamstop_integration.py` | ~220 | UK GAMSTOP integration: registration check, login check, fail-closed handling, marketing suppression, audit logging. |
| `oasis_verification_service.py` | ~270 | Germany OASIS pre-bet verification with caching (Redis pattern). Asymmetric TTL: 5 min for negative, 0 for positive. 24h panic button. |
| `sigap_cpf_checker.py` | ~270 | Brazil SIGAP integration: CPF validation (modulo-11), three touchpoints (registration, daily login, 15-day sweep), account termination workflow. |
| `exclusion_bypass_detector.py` | ~250 | Cross-signal bypass detection: device fingerprint, payment method, IP subnet, email domain, registration proximity, name similarity. Weighted risk scoring. |

## Dependencies

All scripts use:
- `httpx` — HTTP client (async-capable, used synchronously here)
- `structlog` — structured logging

Install: `pip install httpx structlog`

## Running

Each script includes a `_demo()` function invoked via `__main__`. The demos use stub data and do not make real API calls.

```bash
python us_multi_state_exclusion_check.py
python gamstop_integration.py
python oasis_verification_service.py
python sigap_cpf_checker.py
python exclusion_bypass_detector.py
```

## Conventions

Per the book's script conventions (`scripts/CONVENTIONS.md`), suffix chapter scripts live under the parent chapter's directory. These scripts are located at `scripts/chapter-26/self-exclusion-registries/`.
