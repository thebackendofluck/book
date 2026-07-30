# Vendor Integration Stubs

Reference CI stubs for the commercial AppSec, PtaaS, and CNAPP vendors compared
in Chapter 23b section 23b.17. Every stub below is deliberately minimal: enough
to start a trial and upload SARIF to DefectDojo, not enough to hide the policy
decisions each team must make.

All stubs expect DefectDojo (Chapter 23b section 23b.6) to be reachable at the
URL in the `DEFECTDOJO_URL` secret and to hold a product/engagement ID for the
casino platform.

## Secrets required per stub

| File | Secrets |
|---|---|
| `sast-snyk.yml` | `SNYK_TOKEN` |
| `sast-checkmarx.yml` | `CX_TENANT`, `CX_CLIENT_ID`, `CX_CLIENT_SECRET` |
| `sast-sonarcloud.yml` | `SONAR_TOKEN` |
| `sast-aikido.yml` | `AIKIDO_SECRET_KEY` |
| `dast-qualys-was.yml` | `QUALYS_USERNAME`, `QUALYS_PASSWORD`, `QUALYS_WAS_APP_ID` |
| `cnapp-wiz.yml` | `WIZ_CLIENT_ID`, `WIZ_CLIENT_SECRET` |
| `cnapp-defender.yml` | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| `ptaas-cobalt-trigger.sh` | `COBALT_API_TOKEN`, `COBALT_ASSET_ID` |
| `ptaas-hackerone-submit.sh` | `HACKERONE_API_TOKEN`, `HACKERONE_PROGRAM_HANDLE` |
| `ptaas-intruder-scan.sh` | `INTRUDER_API_KEY`, `INTRUDER_TARGET_GROUP_ID` |

## Conventions

- SARIF output is uploaded to DefectDojo via the existing
  `scripts/chapter-23b/security/` tooling where possible.
- Every stub runs on the `main` branch and on pull requests; PtaaS triggers
  fire on release tags only.
- Timeouts are explicit so a hung vendor API cannot block the pipeline.
