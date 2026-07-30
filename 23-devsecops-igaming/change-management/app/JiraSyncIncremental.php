<?php

// AcmetoCasino Change Management - Jira Incremental Sync Command
// Artisan command that synchronizes issues and releases from Jira
// Handles pagination, field mapping, release linking, and bidirectional updates

namespace App\Console\Commands;

use App\Models\FieldInfo;
use App\Models\Issue;
use App\Models\IssueHistory;
use App\Models\JiraSync;
use App\Models\Project;
use App\Models\Release;
use App\Models\ReleaseHistory;
use GrahamCampbell\ResultType\Error;
use GrahamCampbell\ResultType\Result;
use GrahamCampbell\ResultType\Success;
use Illuminate\Console\Command;
use Illuminate\Support\Arr;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\App;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Str;

class JiraSyncIncremental extends Command
{
    // Custom Jira fields for tracking CM sync status
    protected const ADDED_TO_CM_FIELD_ID = 'customfield_10145';
    protected const CM_LAST_SYNC_FIELD_ID = 'customfield_10187';

    protected $argumentName = 'last-updated';
    protected $signature = "jira-sync:incremental-sync {last-updated? : Sync issues updated since the specified timestamp}";
    protected $description = 'Incremental Issue Sync With Jira';

    protected \Illuminate\Support\Collection $idsToSync;

    public function __construct()
    {
        parent::__construct();
        $this->idsToSync = collect();
    }

    /**
     * Get projects configured for Jira synchronization.
     */
    protected function getProjects(): Result
    {
        $projects = Project::with('synchronizedIssueTypes')->has('synchronizedIssueTypes')->get();

        if ($projects->count() === 0) {
            return Error::create([
                'result' => COMMAND::SUCCESS,
                'info' => 'No projects/issue types configured for synchronisation',
            ]);
        }

        return Success::create(['projects' => $projects]);
    }

    /**
     * Determine the last sync timestamp from argument, DB, or user input.
     */
    protected function getLastUpdatedTimeStamp(array $data): Result
    {
        if ($this->argument($this->argumentName) !== null) {
            try {
                $lastUpdated = new Carbon($this->argument($this->argumentName));
            } catch (\Exception $exception) {
                return Error::create([
                    'error' => "Could not parse the '{$this->argumentName}' argument - " . $exception->getMessage()
                ]);
            }
            return Success::create($data + ['lastUpdated' => $lastUpdated]);
        }

        // Fall back to last sync timestamp from database
        if (($lastUpdated = DB::table('jira_syncs')->max('issue_updated')) ||
            ($lastUpdated = DB::table('issues')->max('updated_at'))) {
            return Success::create($data + ['lastUpdated' => new Carbon($lastUpdated)]);
        }

        if (!app()->runningInConsole()) {
            return Error::create([
                'error' => "You must specify a valid '{$this->argumentName}' argument when no previous sync has been run"
            ]);
        }

        try {
            $entered = $this->ask("Sync issues updated since: (yyyy-mm-dd [hh:mm])");
            if (Carbon::canBeCreatedFromFormat($entered, 'Y-m-d')) {
                $entered .= ' 00:00';
            }
            $lastUpdated = Carbon::createFromFormat('Y-m-d H:i', $entered);
        } catch (\Exception $exception) {
            return Error::create([
                'error' => "Could not parse the entered timestamp - " . $exception->getMessage()
            ]);
        }

        return Success::create($data + ['lastUpdated' => $lastUpdated]);
    }

    /**
     * Build JQL query for fetching updated issues across all configured projects.
     */
    protected function buildSearchJql(array $data): Result
    {
        $projectsAndIssueTypes = $data['projects']->map(function ($project) {
            $issueTypeIds = $project->synchronizedIssueTypes()->pluck('jira_id')->join(',');
            return "(project={$project->jira_id} AND issuetype IN ($issueTypeIds))";
        })->join(' OR ');

        $jql = sprintf(
            '(%s) AND updated >= "%s" ORDER BY updated ASC, id ASC',
            $projectsAndIssueTypes,
            $data['lastUpdated']->format('Y/m/d H:i')
        );

        return Success::create($jql);
    }

    protected function buildSearchBody(string $jql): Result
    {
        return Success::create([
            'jql' => $jql,
            'maxResults' => 100,
            'fields' => ['updated'],
            'startAt' => 0,
        ]);
    }

    /**
     * Fetch a page of search results from Jira.
     */
    protected function getIssueSearchResultsPage(array $body): Result
    {
        $jira_url = config('jira_sync.jira_url');
        try {
            $resultsPage = $this->jiraPostRequest("$jira_url/rest/api/3/search", $body);
        } catch (\Exception $exception) {
            return Error::create(['error' => 'Cannot fetch the JIRA issues - Error - ' . $exception->getMessage()]);
        }
        return Success::create($resultsPage);
    }

    /**
     * Upsert fetched issue IDs into the jira_syncs tracking table.
     */
    protected function persistToJiraSyncsTable(array $resultsPage): Result
    {
        $timestamp = now();
        $issuesData = array_map(function ($issue) use ($timestamp) {
            return [
                'jira_id' => $issue['id'],
                'issue_ref' => $issue['key'],
                'issue_updated' => Carbon::parse($issue['fields']['updated'])->floorSeconds(),
                'synchronized' => false,
                'created_at' => $timestamp,
                'updated_at' => $timestamp,
            ];
        }, $resultsPage['issues']);

        try {
            DB::table('jira_syncs')->upsert(
                $issuesData,
                ['jira_id', 'issue_ref', 'issue_updated'],
                ['updated_at']
            );
        } catch (\Exception $exception) {
            return Error::create([
                'error' => 'Failed to insert into jira_syncs - ' . $exception->getMessage(),
            ]);
        }

        return Success::create($resultsPage);
    }

    /**
     * Import a regular issue: create/update issue, sync fields, save history.
     */
    protected function importIssue(array $issueData, \Illuminate\Database\Eloquent\Collection $fieldInfos): void
    {
        $issue = $this->createOrGetIssue($issueData);
        $this->importIssueFields($issue, $issueData, $fieldInfos);

        if (!$issue->wasRecentlyCreated) {
            $this->saveIssueHistory($issue, $issueData);
        }
        $this->line("Issue successfully imported into DB");
    }

    /**
     * Import issue fields: base table fields and EAV fields.
     * Respects field locking (manually edited fields won't be overwritten by Jira sync).
     */
    protected function importIssueFields(Issue $issue, array $issueData, \Illuminate\Database\Eloquent\Collection $fieldInfos): void
    {
        // Base table fields (columns on the issues table)
        foreach ($fieldInfos[true] ?? [] as $fieldInfo) {
            $newValue = $this->getFieldValue($issueData, $fieldInfo);
            $issue->setAttribute($fieldInfo->field_name, $newValue);
        }

        if ($issue->isDirty()) {
            $issue->save();
        }

        // EAV fields (rows in issue_fields table)
        foreach ($fieldInfos[false] ?? [] as $fieldInfo) {
            $newValue = $this->getFieldValue($issueData, $fieldInfo);
            $this->setIssueFieldValue($issue, $fieldInfo->field_name, $newValue);
        }
    }

    /**
     * Write an audit trail entry for an issue that already existed before this
     * sync pass touched it (first-time creation doesn't need a "before" snapshot).
     */
    protected function saveIssueHistory(Issue $issue, array $issueData): void
    {
        $history = new IssueHistory();
        $history->issue_id = $issue->id;
        $history->details = json_encode([
            'changed_by' => 'Jira',
            'jira_updated' => data_get($issueData, 'fields.updated'),
            'data' => $issue->fresh(['fields'])?->toArray(),
        ]);
        $history->save();
    }

    /**
     * Import a release issue: sync fields and link related issues.
     */
    protected function importRelease(array $issueData, \Illuminate\Database\Eloquent\Collection $fieldInfos): void
    {
        $release = $this->createOrGetRelease($issueData);
        $this->importReleaseFields($release, $issueData, $fieldInfos);
        $this->importReleaseLinkedIssues($release, $issueData);

        if (!$release->wasRecentlyCreated) {
            $this->saveReleaseHistory($release, $issueData);
        }
        $this->line("Release issue successfully imported into DB");
    }

    /**
     * Import release fields: base table fields and EAV fields.
     * Mirrors importIssueFields() but writes to the releases/release_fields tables.
     */
    protected function importReleaseFields(Release $release, array $issueData, \Illuminate\Database\Eloquent\Collection $fieldInfos): void
    {
        // Base table fields (columns on the releases table)
        foreach ($fieldInfos[true] ?? [] as $fieldInfo) {
            $newValue = $this->getFieldValue($issueData, $fieldInfo);
            $release->setAttribute($fieldInfo->field_name, $newValue);
        }

        if ($release->isDirty()) {
            $release->save();
        }

        // EAV fields (rows in release_fields table)
        foreach ($fieldInfos[false] ?? [] as $fieldInfo) {
            $newValue = $this->getFieldValue($issueData, $fieldInfo);
            $this->setReleaseFieldValue($release, $fieldInfo->field_name, $newValue);
        }
    }

    /**
     * Write an audit trail entry for a release that already existed before this
     * sync pass touched it (first-time creation doesn't need a "before" snapshot).
     */
    protected function saveReleaseHistory(Release $release, array $issueData): void
    {
        $history = new ReleaseHistory();
        $history->release_id = $release->id;
        $history->details = json_encode([
            'changed_by' => 'Jira',
            'jira_updated' => data_get($issueData, 'fields.updated'),
            'data' => $release->fresh(['fields'])?->toArray(),
        ]);
        $history->save();
    }

    /**
     * Sync release-linked issues from Jira issue links.
     * Handles both inward and outward issue links.
     */
    protected function importReleaseLinkedIssues(Release $release, array $issueData): void
    {
        $release->issues()->sync([]);

        foreach ($issueData['fields']['issuelinks'] as $issueLink) {
            $linkedIssue = null;
            $linkTypeDescription = null;
            if (array_key_exists('inwardIssue', $issueLink)) {
                $linkedIssue = $issueLink['inwardIssue'];
                $linkTypeDescription = $issueLink['type']['inward'];
            } elseif (array_key_exists('outwardIssue', $issueLink)) {
                $linkedIssue = $issueLink['outwardIssue'];
                $linkTypeDescription = $issueLink['type']['outward'];
            }

            if (!empty($linkedIssue)) {
                if ($this->isIssueARelease($linkedIssue)) {
                    $issue = $this->createOrGetRelease($linkedIssue);
                } else {
                    $issue = $this->createOrGetIssue($linkedIssue);
                    $release->issues()->save($issue);
                    $release->issues()->updateExistingPivot($issue->id, [
                        'link_type_description' => Str::ucfirst($linkTypeDescription)
                    ]);
                }

                // Queue linked issues for sync if they have no fields yet
                if ($issue->fields->count() === 0) {
                    $this->addIssueToSyncList($issue);
                }
            }
        }
    }

    /**
     * Paginate through Jira search results and persist IDs to sync table.
     */
    protected function importIdsOfUpdatedIssueFromJira(array $body): Result
    {
        do {
            $result = $this->getIssueSearchResultsPage($body)
                ->flatMap(fn ($resultsPage) => $this->persistToJiraSyncsTable($resultsPage));

            if ($result->error()->isDefined()) {
                return $result;
            }

            $resultsPage = $result->success()->get();
            $pageStats = collect($resultsPage)->only(['startAt', 'maxResults', 'total'])->toArray();

            $body['maxResults'] = $pageStats['maxResults'];
            $body['startAt'] += $pageStats['maxResults'];
        } while ($pageStats['startAt'] + $pageStats['maxResults'] < $pageStats['total']);

        return Success::create(true);
    }

    protected function isIssueARelease(array $issueData): bool
    {
        return Str::startsWith($issueData['key'], config('jira_sync.release_issue_prefix')) &&
            $issueData['fields']['issuetype']['name'] === config('jira_sync.release_issue_type');
    }

    /**
     * Update the "Added to CM" custom field in Jira after successful import.
     * Skipped in local/staging environments.
     */
    protected function updateIssueSyncFieldsInJira(int $jiraIssueId, string $issueKey): void
    {
        $skipJiraUpdate = App::environment(['local', 'staging']);
        if ($skipJiraUpdate) {
            $this->line('JIRA update skipped due to environment');
            return;
        }

        $jira_url = config('jira_sync.jira_url');
        try {
            $this->jiraPutRequest(
                "$jira_url/rest/api/3/issue/$jiraIssueId",
                [
                    'fields' => [
                        self::ADDED_TO_CM_FIELD_ID => ['value' => 'YES', 'id' => '10191'],
                        self::CM_LAST_SYNC_FIELD_ID => (new \DateTimeImmutable())->format(\DateTime::ATOM),
                    ]
                ]
            );
            $this->info("Issue [{$issueKey}] successfully updated in JIRA");
        } catch (\Exception $exception) {
            $this->error("Could not update issue [{$issueKey}] in JIRA - Error - {$exception->getMessage()}");
        }
    }

    protected function getFieldValue(array $issueData, FieldInfo $fieldInfo): mixed
    {
        $value = data_get($issueData, $fieldInfo->jira_name);
        if (Arr::accessible($value)) {
            return collect($value)->join(', ');
        }
        if ($fieldInfo->type === 'datetime') {
            return Carbon::parse($value)->floorSeconds();
        }
        return $value;
    }

    protected function createOrGetIssue(array $issueData): Issue
    {
        $issue = Issue::query()
            ->where('issue_ref', $issueData['key'])
            ->orWhere('jiraId', $issueData['id'])
            ->first();

        if (!$issue) {
            $issue = Issue::create([
                'issue_ref' => $issueData['key'],
                'jiraId' => $issueData['id'],
                'requires_approval' => false,
            ]);
        }

        return $issue;
    }

    protected function createOrGetRelease(array $issueData): Release
    {
        $release = Release::query()
            ->where('release_ref', $issueData['key'])
            ->orWhere('jiraId', $issueData['id'])
            ->first();

        if (!$release) {
            $release = Release::create([
                'release_ref' => $issueData['key'],
                'jiraId' => $issueData['id'],
                'requires_approval' => false,
            ]);
        }

        return $release;
    }

    /**
     * Set an EAV field value on an issue.
     * Respects field locking: manually set fields won't be overwritten by Jira sync.
     */
    protected function setIssueFieldValue(Issue $issue, string $fieldName, mixed $newValue): void
    {
        $issueField = $issue->fields()->firstOrNew(
            ['field_name' => $fieldName],
            ['value_locked' => false, 'created_at' => $issue->updated_at]
        );

        if ($issueField->id === null && empty($newValue)) {
            return;
        }

        if (!$issueField->value_locked) {
            $issueField->field_value = $newValue;
            $issueField->field_value_set_by = 'Jira';
            if ($issueField->isDirty()) {
                $issueField->updated_at = $issue->updated_at;
                $issue->fields()->save($issueField);
            }
        }
    }

    /**
     * Set an EAV field value on a release.
     * Mirrors setIssueFieldValue() but writes to the release_fields table.
     */
    protected function setReleaseFieldValue(Release $release, string $fieldName, mixed $newValue): void
    {
        $releaseField = $release->fields()->firstOrNew(
            ['field_name' => $fieldName],
            ['value_locked' => false, 'created_at' => $release->updated_at]
        );

        if ($releaseField->id === null && empty($newValue)) {
            return;
        }

        if (!$releaseField->value_locked) {
            $releaseField->field_value = $newValue;
            $releaseField->field_value_set_by = 'Jira';
            if ($releaseField->isDirty()) {
                $releaseField->updated_at = $release->updated_at;
                $release->fields()->save($releaseField);
            }
        }
    }

    /**
     * Queue an issue for a follow-up sync pass when it was discovered as a release
     * link but has no fields yet (i.e. it hasn't been fully imported before).
     * Adds it to the in-memory list this run is already processing, and marks it
     * unsynchronized in jira_syncs so a future incremental run picks it up too.
     */
    protected function addIssueToSyncList(Issue $issue): void
    {
        if (empty($issue->jiraId)) {
            return;
        }

        if (!$this->idsToSync->contains($issue->jiraId)) {
            $this->idsToSync->push($issue->jiraId);
        }

        DB::table('jira_syncs')->updateOrInsert(
            [
                'jira_id' => $issue->jiraId,
                'issue_ref' => $issue->issue_ref,
                'issue_updated' => ($issue->updated_at ?? now())->floorSeconds(),
            ],
            [
                'synchronized' => false,
                'created_at' => now(),
                'updated_at' => now(),
            ]
        );
    }

    /**
     * Translate a failed pipeline step into a command exit code.
     * getProjects() uses Error::create(['result' => ..., 'info' => ...]) for a
     * graceful no-op (e.g. nothing configured to sync); every other step returns
     * Error::create(['error' => ...]) for a genuine failure.
     */
    protected function handleResult(Result $result): int
    {
        $error = $result->error()->get();

        if (array_key_exists('result', $error)) {
            if (!empty($error['info'])) {
                $this->line($error['info']);
            }
            return $error['result'];
        }

        $this->error($error['error'] ?? 'Unknown error during Jira sync');
        return self::FAILURE;
    }

    /**
     * Main command handler: orchestrates the full sync pipeline.
     * Uses a monadic Result pattern for clean error handling.
     */
    public function handle(): int
    {
        $result = $this->getProjects()
            ->flatMap(fn ($data) => $this->getLastUpdatedTimeStamp($data))
            ->flatMap(fn ($data) => $this->buildSearchJql($data))
            ->flatMap(fn ($jql) => $this->buildSearchBody($jql))
            ->flatMap(fn ($body) => $this->importIdsOfUpdatedIssueFromJira($body));

        if ($result->error()->isDefined()) {
            return $this->handleResult($result);
        }

        $this->idsToSync = JiraSync::query()
            ->where('synchronized', false)
            ->pluck('jira_id')
            ->unique()
            ->values();

        $this->line('Total issues to be imported/updated: ' . count($this->idsToSync));

        // Load field mappings (grouped by table and whether they're base table fields)
        $fieldInfos = FieldInfo::query()
            ->whereNotNull('jira_name')
            ->whereIn('table', ['issues', 'releases'])
            ->select(['table', 'field_name', 'jira_name', 'is_base_table_field', 'type'])
            ->get()
            ->groupBy(['table', 'is_base_table_field']);

        $fieldNames = $fieldInfos
            ->flatten()
            ->filter(fn ($fieldInfo) => Str::of($fieldInfo->jira_name)->startsWith('fields.'))
            ->pluck('jira_name')
            ->push('issuelinks')
            ->unique()
            ->map(fn ($fieldName) => Str::of(Str::of($fieldName)->after('fields.'))->before('.'))
            ->join(',');

        $jira_url = config('jira_sync.jira_url');

        // Process each issue: fetch from Jira, import, update sync status
        while ($jiraIssueId = $this->idsToSync->shift()) {
            try {
                $url = "$jira_url/rest/api/2/issue/$jiraIssueId?fields=$fieldNames&updateHistory=false&fieldsByKeys=true";
                $issueData = $this->jiraGetRequest($url);
            } catch (\Exception $exception) {
                $this->error('Cannot get the issue data from JIRA - Error - ' . $exception->getMessage());
                return COMMAND::FAILURE;
            }

            $issueTypeDescription = $this->isIssueARelease($issueData) ? 'release' : 'issue';
            $this->line("Importing {$issueTypeDescription} [{$issueData['key']}]");

            DB::beginTransaction();
            try {
                if ($this->isIssueARelease($issueData)) {
                    $this->importRelease($issueData, $fieldInfos['releases'] ?? []);
                } else {
                    $this->importIssue($issueData, $fieldInfos['issues'] ?? []);
                }

                $this->updateIssueSyncFieldsInJira($jiraIssueId, $issueData['key']);
                JiraSync::where('jira_id', $jiraIssueId)->update(['synchronized' => true]);

                DB::commit();
                $this->info("Issue [{$issueData['key']}] successfully imported");
            } catch (\Exception $exception) {
                DB::rollback();
                $this->error("Could not update issue [{$issueData['key']}] in the DB - Error - {$exception->getMessage()}");
            }
        }

        return COMMAND::SUCCESS;
    }

    // --- Jira HTTP Client Methods ---

    protected function jiraGetRequest(string $jira_url)
    {
        return Http::withHeaders(['Accept' => 'application/json'])
            ->withBasicAuth(config('jira_sync.jira_auth_user'), config('jira_sync.jira_auth_password'))
            ->get($jira_url)
            ->throw()
            ->json();
    }

    protected function jiraPostRequest(string $jira_url, array $data)
    {
        return Http::withHeaders(['Accept' => 'application/json'])
            ->withBasicAuth(config('jira_sync.jira_auth_user'), config('jira_sync.jira_auth_password'))
            ->post($jira_url, $data)
            ->throw()
            ->json();
    }

    protected function jiraPutRequest(string $jira_url, array $data)
    {
        return Http::withHeaders(['Accept' => 'application/json'])
            ->withBasicAuth(config('jira_sync.jira_auth_user'), config('jira_sync.jira_auth_password'))
            ->put($jira_url, $data)
            ->throw()
            ->json();
    }
}
