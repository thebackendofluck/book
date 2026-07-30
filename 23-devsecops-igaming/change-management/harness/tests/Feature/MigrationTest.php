<?php

use Illuminate\Support\Facades\Schema;

test('migrate:fresh creates the full chapter-23 change-management schema', function () {
    // RefreshDatabase has already run the full migrate:fresh cycle (including the
    // symlinked create_base_tables.php anonymous migration) before this test body
    // executes; we assert every table it is expected to create is present.
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
        expect(Schema::hasTable($table))->toBeTrue("Expected table [$table] to exist after migrate:fresh");
    }
});
