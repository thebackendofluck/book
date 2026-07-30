# chapter-23 change-management — Laravel 12 e2e harness

This is a disposable Laravel 12 application whose only purpose is to **prove** that
the chapter-23 sample files in `../app` and `../database` actually run on Laravel 12 /
PHP 8.2+. It is not part of the book narrative — it's a test rig.

## How the sample files get here

The chapter's sample files are **symlinked** in, not copied, so the tests always
exercise the real, up-to-date sample code:

| Harness path | -> | Sample source |
|---|---|---|
| `app/Models/Issue.php` | -> | `../app/Issue.php` |
| `app/Models/IssueField.php` | -> | `../app/Models/IssueField.php` |
| `app/Models/IssueHistory.php` | -> | `../app/Models/IssueHistory.php` |
| `app/Models/IssueEnvironment.php` | -> | `../app/Models/IssueEnvironment.php` |
| `app/Models/IssueJurisdiction.php` | -> | `../app/Models/IssueJurisdiction.php` |
| `app/Models/Environment.php` | -> | `../app/Models/Environment.php` |
| `app/Models/Jurisdiction.php` | -> | `../app/Models/Jurisdiction.php` |
| `app/Models/ApprovalType.php` | -> | `../app/Models/ApprovalType.php` |
| `app/Models/Document.php` | -> | `../app/Models/Document.php` |
| `app/Models/Component.php` | -> | `../app/Models/Component.php` |
| `app/Models/ComponentField.php` | -> | `../app/Models/ComponentField.php` |
| `app/Models/ComponentVersion.php` | -> | `../app/Models/ComponentVersion.php` |
| `app/Models/FieldInfo.php` | -> | `../app/Models/FieldInfo.php` |
| `app/Models/Release.php` | -> | `../app/Models/Release.php` |
| `app/Models/ReleaseField.php` | -> | `../app/Models/ReleaseField.php` |
| `app/Models/ReleaseHistory.php` | -> | `../app/Models/ReleaseHistory.php` |
| `app/Models/Project.php` | -> | `../app/Models/Project.php` |
| `app/Models/ProjectIssueType.php` | -> | `../app/Models/ProjectIssueType.php` |
| `app/Models/JiraSync.php` | -> | `../app/Models/JiraSync.php` |
| `app/Http/Controllers/IssuesController.php` | -> | `../app/IssuesController.php` |
| `app/Http/Controllers/ComponentsController.php` | -> | `../app/ComponentsController.php` |
| `app/Console/Commands/JiraSyncIncremental.php` | -> | `../app/JiraSyncIncremental.php` |
| `database/migrations/2024_01_01_000010_create_base_tables.php` | -> | `../database/create_base_tables.php` |

Everything else under `app/`, `database/migrations/`, `database/factories/`,
`routes/api.php`, and `config/jira_sync.php` is harness-only scaffolding
(controllers/commands/routes/config a real app would also have, reconstructed from
how the sample files use them, since the book excerpt only ships those files). The
one exception is `app/Models/User.php`, which models the harness-only `users` table
shape and is never part of the book narrative.

## Gaps resolved

The chapter-23 sample originally referenced methods, classes, and columns it never
shipped (32 pre-existing `phpstan` gaps). These are now fully implemented as part of
the sample rather than worked around in the harness:

1. **`Issue`'s relations** (`components()`, `documents()`, `environments()`,
   `fields()`) now resolve against real sibling models under `app/Models/`
   (`IssueField`, `Component`, `ComponentVersion`, `IssueEnvironment`,
   `IssueJurisdiction`, `Document`), including the two new lookup models
   (`Environment`, `Jurisdiction`) and `ApprovalType` that back the
   jurisdiction-aware approval workflow (`Issue::updateFields()`).
2. **`JiraSyncIncremental`** now implements `saveIssueHistory()`,
   `saveReleaseHistory()`, `importReleaseFields()` (+ its `setReleaseFieldValue()`
   helper), `addIssueToSyncList()`, and `handleResult()` — see
   `app/Console/Commands/JiraSyncIncremental.php`.
3. **`issues.jiraId`, `releases.jiraId`, `release_issues.link_type_description`**,
   the `environments` / `jurisdictions` / `approval_types` / `issue_environments` /
   `issue_jurisdictions` tables, the `projects` / `project_issue_types` /
   `jira_syncs` tables, and `documents.storage_path` are now first-class columns/
   tables in `database/create_base_tables.php` itself (previously patched in
   harness-only migrations).

Two pre-existing, narrower gaps remain deliberately un-"fixed" here (fixing them
would mean inventing schema/business logic never part of the book excerpt):

- **`ComponentsController::create()` never sets `$version->released`**, but
  `component_versions.released` is a required (non-nullable) column. Patched
  harness-only (nullable) via `2024_01_01_000025_patch_component_versions_released_nullable.php`.
- **`users`** table (from `create_base_tables.php`) only has `id`, `username`,
  `last_login` — no `created_at`/`updated_at`. The harness `User` model sets
  `public $timestamps = false;` to match.

## Running it

```bash
composer install
php artisan migrate:fresh       # SQLite file DB, for manual poking
./vendor/bin/pest               # Pest feature/unit tests, SQLite :memory: (phpunit.xml)
./vendor/bin/phpstan analyse --memory-limit=1G   # 0 errors
```

## Running it in Docker (real HTTP)

The sample root ships a `Dockerfile` + `docker-compose.yml` that serve the harness
over HTTP (the build context must be the sample root — this directory's app/Models
and migrations are symlinks into `../app` and `../database`):

```bash
cd ..                            # change-management/ root
docker compose up -d --build     # migrates on boot, serves on port 8000
curl http://<host-ip>:8523/up    # Laravel health check
```

Notes:

- `docker-compose.yml` publishes to an explicit host address, never `0.0.0.0`
  (published ports bypass the host INPUT firewall). Adjust the address for your host.
- The image runs with `APP_DEBUG=false` (debug pages leak traces/paths/SQL) and
  generates `APP_KEY` at container start so no secret is baked into image layers.
- `GET /` is a JSON status page; `public/index.php` exists only so `artisan serve`
  works — the rig has no frontend views.
- `POST /api/login {username}` is a **harness-only** session login (the chapter-23
  `users` table has no password column; the route 404s in production). It exists so
  curl/browser runs can exercise the same session `auth` guard the tests drive with
  `actingAs()`:

```bash
curl -c /tmp/jar -X POST -H 'Content-Type: application/json' \
  -d '{"username":"demo"}' http://<host-ip>:8523/api/login
curl -b /tmp/jar -X POST -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"issue_ref":"CM-1","title":"First issue"}' http://<host-ip>:8523/api/issues
```

## Test coverage

- `tests/Feature/MigrationTest.php` — all tables from `create_base_tables.php` exist.
- `tests/Unit/MigrationReversibilityTest.php` — `down()` drops the full schema cleanly
  (run outside RefreshDatabase's transaction wrapper — SQLite can't `VACUUM` inside one).
- `tests/Feature/IssuesControllerTest.php` — `POST /api/issues` creates an issue +
  its transactional EAV fields; failure path rolls back cleanly.
- `tests/Feature/ComponentsControllerTest.php` — `POST /api/components` registers a
  component + optional initial version.
- `tests/Feature/IssueScopeTest.php` — `Issue::unclosed()`/`Issue::closed()` scopes.
- `tests/Feature/IssueUpdateFieldsTest.php` — `Issue::updateFields()` end-to-end:
  base-table + EAV field updates, component attach/detach with version tracking,
  environment + jurisdiction approval creation, document upload/attach, and the
  `IssueHistory` audit trail; `Issue::deleteIssue()` audit + soft-delete.
- `tests/Feature/DocumentTest.php` — `Document::uploadDocument()` stores file
  contents on disk and records metadata; skips storage when no contents are given.
- `tests/Feature/JiraSyncIncrementalTest.php` — `Http::fake()`-mocked Jira API (no
  real network): search pagination (3 pages), field mapping (base-table + EAV,
  driven by `field_info.jira_name`), incremental timestamp, sync bookkeeping
  (`jira_syncs`), release<->issue linking (`importReleaseLinkedIssues()`),
  `addIssueToSyncList()` for newly-discovered linked issues, `saveIssueHistory()`
  (both directly and via a real re-sync pass), `importReleaseFields()` +
  `saveReleaseHistory()`, and `handleResult()`'s graceful-no-op vs. genuine-failure
  branches.
