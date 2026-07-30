<?php

use App\Console\Commands\JiraSyncIncremental;
use App\Models\Issue;
use App\Models\IssueHistory;
use App\Models\JiraSync;
use App\Models\Project;
use App\Models\Release;
use App\Models\ReleaseField;
use App\Models\ReleaseHistory;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\Http;

/**
 * These tests exercise the real, namespace-fixed app/JiraSyncIncremental.php sample
 * against a mocked Jira HTTP API (Http::fake() — no real network).
 *
 * JiraSyncIncremental::handle()/importRelease()/importReleaseLinkedIssues() call
 * $this->saveIssueHistory(), $this->saveReleaseHistory(), $this->importReleaseFields(),
 * $this->addIssueToSyncList() and $this->handleResult() — all of which are now
 * implemented (see app/JiraSyncIncremental.php). The tests below cover:
 *   - the full handle() pipeline (pagination, field mapping, incremental timestamp,
 *     sync bookkeeping) for first-time issue creation,
 *   - re-syncing an already-imported issue, which drives saveIssueHistory(),
 *   - importReleaseLinkedIssues() (release<->issue linking) directly via reflection,
 *   - addIssueToSyncList(), directly via reflection, for a linked issue discovered
 *     with zero fields,
 *   - importReleaseFields()/saveReleaseHistory(), directly via reflection, and
 *   - handleResult()'s two branches (graceful no-op vs. genuine failure), driven
 *     through the real command pipeline so Command output is properly initialized.
 */
function seedJiraSyncProjectFieldMapping(): Project
{
    $project = Project::create(['jira_id' => '10001', 'name' => 'Casino Platform']);
    $project->synchronizedIssueTypes()->create(['jira_id' => '10101', 'name' => 'Bug']);

    \App\Models\FieldInfo::create([
        'title' => 'Requires Approval',
        'field_name' => 'requires_approval',
        'table' => 'issues',
        'is_base_table_field' => true,
        'show_in_list' => true,
        'can_edit' => true,
        'can_filter' => true,
        'jira_name' => 'fields.priority.name',
        'type' => 'text',
        'order' => 1,
        'list_order' => 1,
    ]);

    \App\Models\FieldInfo::create([
        'title' => 'CM Status',
        'field_name' => 'cm_status',
        'table' => 'issues',
        'is_base_table_field' => false,
        'show_in_list' => true,
        'can_edit' => true,
        'can_filter' => true,
        'jira_name' => 'fields.status.name',
        'type' => 'text',
        'order' => 2,
        'list_order' => 2,
    ]);

    \App\Models\FieldInfo::create([
        'title' => 'Summary',
        'field_name' => 'summary',
        'table' => 'issues',
        'is_base_table_field' => false,
        'show_in_list' => true,
        'can_edit' => true,
        'can_filter' => true,
        'jira_name' => 'fields.summary',
        'type' => 'text',
        'order' => 3,
        'list_order' => 3,
    ]);

    return $project;
}

function jiraIssueDetail(string $id, string $key, string $priority, string $status, string $summary, string $updated): array
{
    return [
        'id' => $id,
        'key' => $key,
        'fields' => [
            'issuetype' => ['name' => 'Bug'],
            'updated' => $updated,
            'priority' => ['name' => $priority],
            'status' => ['name' => $status],
            'summary' => $summary,
            'issuelinks' => [],
        ],
    ];
}

test('incremental sync paginates the Jira search, maps fields, and records the incremental timestamp', function () {
    $jiraUrl = config('jira_sync.jira_url');
    seedJiraSyncProjectFieldMapping();

    $searchPage1 = [
        'startAt' => 0,
        'maxResults' => 2,
        'total' => 5,
        'issues' => [
            ['id' => '2001', 'key' => 'CM-2001', 'fields' => ['updated' => '2026-07-01T09:00:00.000+0000']],
            ['id' => '2002', 'key' => 'CM-2002', 'fields' => ['updated' => '2026-07-01T09:05:00.000+0000']],
        ],
    ];
    $searchPage2 = [
        'startAt' => 2,
        'maxResults' => 2,
        'total' => 5,
        'issues' => [
            ['id' => '2003', 'key' => 'CM-2003', 'fields' => ['updated' => '2026-07-01T09:10:00.000+0000']],
            ['id' => '2004', 'key' => 'CM-2004', 'fields' => ['updated' => '2026-07-01T09:15:00.000+0000']],
        ],
    ];
    $searchPage3 = [
        'startAt' => 4,
        'maxResults' => 2,
        'total' => 5,
        'issues' => [
            ['id' => '2005', 'key' => 'CM-2005', 'fields' => ['updated' => '2026-07-01T09:20:00.000+0000']],
        ],
    ];

    Http::fake([
        "$jiraUrl/rest/api/3/search" => Http::sequence()
            ->push($searchPage1)
            ->push($searchPage2)
            ->push($searchPage3),
        "$jiraUrl/rest/api/2/issue/2001*" => Http::response(jiraIssueDetail('2001', 'CM-2001', 'High', 'In Progress', 'First issue', '2026-07-01T09:00:00.000+0000')),
        "$jiraUrl/rest/api/2/issue/2002*" => Http::response(jiraIssueDetail('2002', 'CM-2002', 'Low', 'Open', 'Second issue', '2026-07-01T09:05:00.000+0000')),
        "$jiraUrl/rest/api/2/issue/2003*" => Http::response(jiraIssueDetail('2003', 'CM-2003', 'Medium', 'Open', 'Third issue', '2026-07-01T09:10:00.000+0000')),
        "$jiraUrl/rest/api/2/issue/2004*" => Http::response(jiraIssueDetail('2004', 'CM-2004', 'High', 'In Progress', 'Fourth issue', '2026-07-01T09:15:00.000+0000')),
        "$jiraUrl/rest/api/2/issue/2005*" => Http::response(jiraIssueDetail('2005', 'CM-2005', 'Low', 'Open', 'Fifth issue', '2026-07-01T09:20:00.000+0000')),
        "$jiraUrl/rest/api/3/issue/*" => Http::response([], 200),
    ]);

    $exitCode = \Illuminate\Support\Facades\Artisan::call('jira-sync:incremental-sync', [
        'last-updated' => '2026-06-01 00:00',
    ]);

    expect($exitCode)->toBe(0);

    // --- Pagination: 3 search pages + 5 issue GETs + 5 issue-sync PUTs were sent ---
    Http::assertSentCount(13);
    $searchRequests = collect(Http::recorded(
        fn ($request, $response) => str_starts_with($request->url(), "$jiraUrl/rest/api/3/search")
    ));
    expect($searchRequests)->toHaveCount(3);

    // --- Incremental timestamp: the JQL sent on the first page reflects the explicit `last-updated` argument ---
    $firstSearchRequest = $searchRequests->first()[0];
    expect($firstSearchRequest['jql'])->toContain('updated >= "2026/06/01 00:00"');
    expect($firstSearchRequest['jql'])->toContain('project=10001');
    expect($firstSearchRequest['jql'])->toContain('issuetype IN (10101)');

    // --- Pagination advanced startAt correctly across pages ---
    $secondSearchRequest = $searchRequests->get(1)[0];
    $thirdSearchRequest = $searchRequests->get(2)[0];
    expect($secondSearchRequest['startAt'])->toBe(2);
    expect($thirdSearchRequest['startAt'])->toBe(4);

    // --- All 5 pages of issue ids were persisted into jira_syncs and marked synchronized ---
    expect(JiraSync::count())->toBe(5);
    expect(JiraSync::where('synchronized', true)->count())->toBe(5);

    // --- Field mapping: base-table field (requires_approval) + EAV fields (cm_status, summary) ---
    $issue = Issue::where('issue_ref', 'CM-2001')->firstOrFail();
    expect($issue->jiraId)->toBe('2001');
    expect($issue->requires_approval)->toBe('High'); // mapped from fields.priority.name (base table field)
    expect($issue->fields()->where('field_name', 'cm_status')->value('field_value'))->toBe('In Progress');
    expect($issue->fields()->where('field_name', 'cm_status')->value('field_value_set_by'))->toBe('Jira');
    expect($issue->fields()->where('field_name', 'summary')->value('field_value'))->toBe('First issue');

    $secondIssue = Issue::where('issue_ref', 'CM-2002')->firstOrFail();
    expect($secondIssue->requires_approval)->toBe('Low');
    expect($secondIssue->fields()->where('field_name', 'cm_status')->value('field_value'))->toBe('Open');

    expect(Issue::count())->toBe(5);
});

test('importReleaseLinkedIssues() links an existing issue to a release with its link-type description', function () {
    $release = Release::create(['release_ref' => 'REL-9001', 'jiraId' => '9001', 'requires_approval' => false]);

    // Pre-seed the linked issue WITH an existing field, so that createOrGetIssue()
    // finds it (rather than creating a brand-new issue with zero fields, which would
    // hit the undefined $this->addIssueToSyncList() call documented above).
    $linkedIssue = Issue::factory()->create(['issue_ref' => 'CM-3001', 'jiraId' => '3001']);
    $linkedIssue->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'In Progress',
        'field_value_set_by' => 'seed',
    ]);

    $issueData = [
        'fields' => [
            'issuelinks' => [
                [
                    'type' => ['inward' => 'blocks', 'outward' => 'is blocked by'],
                    'inwardIssue' => [
                        'id' => '3001',
                        'key' => 'CM-3001',
                        'fields' => ['issuetype' => ['name' => 'Bug']],
                    ],
                ],
            ],
        ],
    ];

    $command = new JiraSyncIncremental();
    $method = new ReflectionMethod($command, 'importReleaseLinkedIssues');
    $method->setAccessible(true);
    $method->invoke($command, $release, $issueData);

    expect($release->issues()->count())->toBe(1);

    $pivot = $release->issues()->first()->pivot;
    expect($pivot->link_type_description)->toBe('Blocks');

    $linked = $release->issues()->first();
    expect($linked->issue_ref)->toBe('CM-3001');
    expect($linked->id)->toBe($linkedIssue->id); // proves the *existing* issue was reused, not duplicated
});

test('addIssueToSyncList() queues a linked issue with no fields yet for a follow-up sync pass', function () {
    // Mirrors the previous test's linking scenario, but this issue has ZERO fields,
    // so importReleaseLinkedIssues() takes the branch that calls addIssueToSyncList()
    // directly (exercised here in isolation via reflection).
    $issue = Issue::factory()->create(['issue_ref' => 'CM-6001', 'jiraId' => '6001']);

    $command = new JiraSyncIncremental();
    $method = new ReflectionMethod($command, 'addIssueToSyncList');
    $method->setAccessible(true);
    $method->invoke($command, $issue);

    $idsToSyncProperty = new ReflectionProperty($command, 'idsToSync');
    $idsToSyncProperty->setAccessible(true);
    $idsToSync = $idsToSyncProperty->getValue($command);

    expect($idsToSync->contains('6001'))->toBeTrue();
    expect(JiraSync::where('jira_id', '6001')->where('synchronized', false)->exists())->toBeTrue();
});

test('addIssueToSyncList() is a no-op for an issue with no jiraId', function () {
    $issue = Issue::factory()->create(['issue_ref' => 'CM-6002', 'jiraId' => null]);

    $command = new JiraSyncIncremental();
    $method = new ReflectionMethod($command, 'addIssueToSyncList');
    $method->setAccessible(true);
    $method->invoke($command, $issue);

    $idsToSyncProperty = new ReflectionProperty($command, 'idsToSync');
    $idsToSyncProperty->setAccessible(true);

    expect($idsToSyncProperty->getValue($command))->toHaveCount(0);
    expect(JiraSync::count())->toBe(0);
});

test('saveIssueHistory() records a JSON snapshot of the issue keyed to its id', function () {
    $issue = Issue::factory()->create(['issue_ref' => 'CM-7001', 'jiraId' => '7001']);
    $issue->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'Open',
        'field_value_set_by' => 'seed',
    ]);

    $command = new JiraSyncIncremental();
    $method = new ReflectionMethod($command, 'saveIssueHistory');
    $method->setAccessible(true);
    $method->invoke($command, $issue, ['fields' => ['updated' => '2026-07-01T10:00:00.000+0000']]);

    expect(IssueHistory::where('issue_id', $issue->id)->count())->toBe(1);

    $history = IssueHistory::where('issue_id', $issue->id)->firstOrFail();
    $details = json_decode($history->details, true);
    expect($details['changed_by'])->toBe('Jira');
    expect($details['jira_updated'])->toBe('2026-07-01T10:00:00.000+0000');
    expect($details['data']['issue_ref'])->toBe('CM-7001');
});

test('re-syncing an already-imported issue records an audit entry via saveIssueHistory()', function () {
    seedJiraSyncProjectFieldMapping();
    $jiraUrl = config('jira_sync.jira_url');

    $existing = Issue::factory()->create(['issue_ref' => 'CM-7002', 'jiraId' => '7002']);

    Http::fake([
        "$jiraUrl/rest/api/3/search" => Http::response([
            'startAt' => 0, 'maxResults' => 100, 'total' => 1,
            'issues' => [
                ['id' => '7002', 'key' => 'CM-7002', 'fields' => ['updated' => '2026-07-02T09:00:00.000+0000']],
            ],
        ]),
        "$jiraUrl/rest/api/2/issue/7002*" => Http::response(
            jiraIssueDetail('7002', 'CM-7002', 'High', 'In Progress', 'Updated summary', '2026-07-02T09:00:00.000+0000')
        ),
        "$jiraUrl/rest/api/3/issue/*" => Http::response([], 200),
    ]);

    $exitCode = Artisan::call('jira-sync:incremental-sync', ['last-updated' => '2026-07-01 00:00']);

    expect($exitCode)->toBe(0);
    expect(IssueHistory::where('issue_id', $existing->id)->count())->toBe(1);
});

test('importReleaseFields() imports EAV fields onto a release from mapped Jira data', function () {
    $release = Release::create(['release_ref' => 'REL-7003', 'jiraId' => '7003', 'requires_approval' => false]);

    ReleaseField::query(); // ensure autoload before grouping below

    \App\Models\FieldInfo::create([
        'title' => 'Release Notes',
        'field_name' => 'release_notes',
        'table' => 'releases',
        'is_base_table_field' => false,
        'show_in_list' => true,
        'can_edit' => true,
        'can_filter' => false,
        'jira_name' => 'fields.summary',
        'type' => 'text',
        'order' => 1,
        'list_order' => 1,
    ]);

    $fieldInfos = \App\Models\FieldInfo::where('table', 'releases')->get()->groupBy('is_base_table_field');
    $issueData = ['fields' => ['summary' => 'Release notes text from Jira']];

    $command = new JiraSyncIncremental();
    $method = new ReflectionMethod($command, 'importReleaseFields');
    $method->setAccessible(true);
    $method->invoke($command, $release, $issueData, $fieldInfos);

    expect($release->fields()->where('field_name', 'release_notes')->value('field_value'))
        ->toBe('Release notes text from Jira');
    expect($release->fields()->where('field_name', 'release_notes')->value('field_value_set_by'))
        ->toBe('Jira');
});

test('saveReleaseHistory() records a JSON snapshot of the release keyed to its id', function () {
    $release = Release::create(['release_ref' => 'REL-7004', 'jiraId' => '7004', 'requires_approval' => false]);
    $release->fields()->create([
        'field_name' => 'release_notes',
        'field_value' => 'v2.4.0',
        'field_value_set_by' => 'seed',
    ]);

    $command = new JiraSyncIncremental();
    $method = new ReflectionMethod($command, 'saveReleaseHistory');
    $method->setAccessible(true);
    $method->invoke($command, $release, ['fields' => ['updated' => '2026-07-03T11:00:00.000+0000']]);

    expect(ReleaseHistory::where('release_id', $release->id)->count())->toBe(1);

    $history = ReleaseHistory::where('release_id', $release->id)->firstOrFail();
    $details = json_decode($history->details, true);
    expect($details['changed_by'])->toBe('Jira');
    expect($details['jira_updated'])->toBe('2026-07-03T11:00:00.000+0000');
    expect($details['data']['release_ref'])->toBe('REL-7004');
});

test('handle() gracefully no-ops via handleResult() when no projects are configured for sync', function () {
    // No Project::synchronizedIssueTypes seeded at all -> getProjects() returns a
    // graceful no-op Error (result => Command::SUCCESS), routed through handleResult().
    Http::fake();

    $exitCode = Artisan::call('jira-sync:incremental-sync', ['last-updated' => '2026-06-01 00:00']);

    expect($exitCode)->toBe(\Illuminate\Console\Command::SUCCESS);
    Http::assertNothingSent();
});

test('handle() reports a genuine failure via handleResult() for an unparseable last-updated argument', function () {
    seedJiraSyncProjectFieldMapping();

    $exitCode = Artisan::call('jira-sync:incremental-sync', ['last-updated' => 'not-a-real-timestamp-!!']);

    expect($exitCode)->toBe(\Illuminate\Console\Command::FAILURE);
});
