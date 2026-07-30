<?php

use Illuminate\Support\Facades\Schema;

test('the create_base_tables anonymous migration is reversible via down()', function () {
    // Run outside of RefreshDatabase's transaction wrapper (see Pest.php) so that
    // migrate:fresh's SQLite VACUUM step doesn't conflict with an open transaction.
    $this->artisan('migrate:fresh')->assertExitCode(0);
    expect(Schema::hasTable('issues'))->toBeTrue();
    expect(Schema::hasTable('field_info'))->toBeTrue();
    expect(Schema::hasTable('jira_syncs'))->toBeTrue();

    // Roll back the 1 remaining harness-only patch migration (the
    // component_versions.released nullable patch; add_jira_sync_columns and
    // create_jira_sync_support_tables were folded into create_base_tables.php
    // itself once the sample's jiraId columns / projects / jira_syncs tables
    // became first-class parts of the schema), then roll back create_base_tables
    // itself and confirm down() drops the full schema cleanly, in FK-safe order.
    $this->artisan('migrate:rollback', ['--step' => 1])->assertExitCode(0);
    expect(Schema::hasTable('issues'))->toBeTrue(); // create_base_tables itself not yet rolled back
    expect(Schema::hasTable('jira_syncs'))->toBeTrue();

    $this->artisan('migrate:rollback', ['--step' => 1])->assertExitCode(0);

    foreach ([
        'issues',
        'issue_fields',
        'releases',
        'release_fields',
        'release_issues',
        'documents',
        'approvers',
        'issue_approvals',
        'issue_documents',
        'release_documents',
        'components',
        'component_versions',
        'component_fields',
        'component_history',
        'issue_history',
        'release_history',
        'issue_components',
        'field_info',
        'environments',
        'jurisdictions',
        'approval_types',
        'issue_environments',
        'issue_jurisdictions',
        'projects',
        'project_issue_types',
        'jira_syncs',
        'users',
    ] as $table) {
        expect(Schema::hasTable($table))->toBeFalse("Expected table [$table] to be dropped by down()");
    }

    // Leave the schema migrated for any subsequent test in this process.
    $this->artisan('migrate')->assertExitCode(0);
});
