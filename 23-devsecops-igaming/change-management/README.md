# Change Management System

## Overview

Production change management system for tracking software releases, approvals, and compliance in a regulated iGaming environment. Built with Laravel 12 (PHP 8.2+) + Vue.js SPA + GraphQL. Integrates with Jira for issue synchronization and Keycloak for SAML SSO authentication.

> **Runnable harness:** `harness/` contains a minimal Laravel 12 app that boots these
> sample files (via symlinks, so it always runs the real, current sample code) and
> exercises them with Pest feature/unit tests — migrations, the REST endpoints, the
> Eloquent scopes/relations, the jurisdiction-aware approval workflow, document
> uploads, and the Jira sync command against a mocked HTTP client. `phpstan` is clean
> (0 errors) and every model/method the sample references now ships as real code
> under `app/Models/`. See `harness/README.md` for how to run it and for the two
> narrower, deliberately unfixed gaps that predate the Laravel 12 migration.

## Architecture

```
Vue.js SPA  <-->  Laravel API (REST + GraphQL)  <-->  PostgreSQL
  (spa/)           (app/)                              (database/migrations)
                     |
              External Services
              (Jira, Keycloak, AWS S3)
```

### Key Features
- Jira bidirectional sync (incremental, scheduled)
- Multi-environment approval workflows with jurisdiction tracking
- Component versioning with checksum validation
- Release-to-issue linking with audit trails
- Keycloak + SAML SSO authentication
- GraphQL API with NuWave Lighthouse
- Soft-delete with full history logging

## Files

| File | Description |
|------|-------------|
| `app/Issue.php` | Eloquent model (`namespace App\Models`) with scoped queries, field filtering, jurisdiction/environment relationships |
| `app/IssuesController.php` | REST controller for issue creation with transactional field management |
| `app/ComponentsController.php` | REST controller for software component registration and versioning |
| `app/JiraSyncIncremental.php` | Artisan command for incremental Jira sync with pagination, field mapping, release linking, and history audit trails |
| `app/Models/` | Supporting Eloquent models `Issue` relies on: EAV fields (`IssueField`, `ReleaseField`, `ComponentField`), audit history (`IssueHistory`, `ReleaseHistory`), components/releases (`Component`, `ComponentVersion`, `Release`), the jurisdiction-aware approval workflow (`IssueEnvironment`, `IssueJurisdiction`, `Environment`, `Jurisdiction`, `ApprovalType`), attachments (`Document`), field metadata (`FieldInfo`), and Jira sync bookkeeping (`Project`, `ProjectIssueType`, `JiraSync`) |
| `database/create_base_tables.php` | Anonymous-class migration (Laravel 9+ style) defining the full schema: issues, releases, components, approvals, documents, jurisdiction-aware environment approvals, and Jira sync bookkeeping |
| `composer.json` | PHP dependencies: Laravel 12, Lighthouse GraphQL, Keycloak guard, SAML2, Excel exports |
| `harness/` | Disposable Laravel 12 app proving the files above run — see `harness/README.md` |

## Tech Stack
- **Backend:** Laravel 12, PHP 8.2+
- **Frontend:** Vue.js 2.6, Vuex, Vue Router
- **API:** GraphQL (Lighthouse) + REST
- **Database:** PostgreSQL with Flyway-style migrations
- **Auth:** Keycloak + SAML2 SSO
- **Integrations:** Jira REST API, AWS S3
- **Infra:** Docker, Laradock, Apache
