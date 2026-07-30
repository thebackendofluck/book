<?php

/**
 * Harness-only config. JiraSyncIncremental reads config('jira_sync.*') for the Jira
 * base URL/credentials and the naming convention used to detect "release" issues.
 * Not part of the 5 chapter-23 sample files; reconstructed from the config() calls
 * in app/JiraSyncIncremental.php so the command can run in tests with Http::fake().
 */
return [
    'jira_url' => env('JIRA_SYNC_URL', 'https://jira.example.test'),
    'jira_auth_user' => env('JIRA_SYNC_AUTH_USER', 'jira-sync-bot'),
    'jira_auth_password' => env('JIRA_SYNC_AUTH_PASSWORD', 'testing-token'),
    'release_issue_prefix' => env('JIRA_SYNC_RELEASE_PREFIX', 'REL-'),
    'release_issue_type' => env('JIRA_SYNC_RELEASE_TYPE', 'Release'),
];
