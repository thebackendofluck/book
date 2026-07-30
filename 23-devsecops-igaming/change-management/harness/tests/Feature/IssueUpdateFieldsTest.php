<?php

use App\Models\ApprovalType;
use App\Models\Component;
use App\Models\Environment;
use App\Models\FieldInfo;
use App\Models\Issue;
use App\Models\IssueHistory;
use App\Models\Jurisdiction;
use App\Models\User;
use Illuminate\Support\Facades\Storage;

/**
 * Exercises Issue::updateFields()/deleteIssue() end-to-end against the models that
 * used to be missing from the sample (IssueField, IssueHistory, Component,
 * ComponentVersion, IssueEnvironment, IssueJurisdiction, Document) and the relations
 * that referenced them (Issue::components()/documents()/environments()/fields()).
 */
function seedEditableIssueFields(): void
{
    FieldInfo::create([
        'title' => 'Requires Approval',
        'field_name' => 'requires_approval',
        'table' => 'issues',
        'is_base_table_field' => true,
        'show_in_list' => true,
        'can_edit' => true,
        'can_filter' => true,
        'jira_name' => null,
        'type' => 'text',
        'order' => 1,
        'list_order' => 1,
    ]);

    FieldInfo::create([
        'title' => 'Notes',
        'field_name' => 'notes',
        'table' => 'issues',
        'is_base_table_field' => false,
        'show_in_list' => true,
        'can_edit' => true,
        'can_filter' => true,
        'jira_name' => null,
        'type' => 'text',
        'order' => 2,
        'list_order' => 2,
    ]);
}

test('Issue::updateFields() updates base-table and EAV fields, attaches a component, creates an environment with a jurisdiction approval, uploads a document, and writes an audit entry', function () {
    Storage::fake('local');
    seedEditableIssueFields();

    $user = User::factory()->create(['username' => 'compliance-officer']);
    $this->actingAs($user);

    $issue = Issue::factory()->create(['issue_ref' => 'CM-8001', 'requires_approval' => 'None']);
    $issue->fields()->create([
        'field_name' => 'notes',
        'field_value' => 'old note',
        'field_value_set_by' => 'seed',
    ]);

    $component = Component::create(['name' => 'payments-gateway-service']);
    $environment = Environment::create(['name' => 'Production']);
    $jurisdiction = Jurisdiction::create(['code' => 'UKGC', 'name' => 'UK Gambling Commission']);
    $approvalType = ApprovalType::create(['name' => 'Regulatory Approval Required']);

    $result = $issue->updateFields(null, [
        'id' => (string) $issue->id, // GraphQL-style string id, must be cast to int internally
        'requires_approval' => 'Yes',
        'fields' => [
            ['field_name' => 'notes', 'value' => 'updated note', 'locked' => false],
        ],
        'components' => [
            ['id' => $component->id, 'version' => '1.2.0', 'checksum' => 'abc123'],
        ],
        'environments' => [
            [
                'environment_id' => $environment->id,
                'jurisdictions' => [
                    [
                        'jurisdiction_id' => $jurisdiction->id,
                        'approval_type_id' => $approvalType->id,
                        'approval_sent_on' => '2026-07-01',
                        'approval_received_on' => null,
                    ],
                ],
            ],
        ],
        'files' => [
            [
                'file_name' => 'impact-assessment.pdf',
                'file_type' => 'application/pdf',
                'description' => 'DPIA for the new payment provider',
                'contents' => 'PDF-BYTES',
            ],
        ],
        'files_to_remove' => [],
    ]);

    expect($result)->toBe(['has_updated' => true, 'error' => null]);

    $issue->refresh();
    expect($issue->requires_approval)->toBe('Yes');
    expect($issue->fields()->where('field_name', 'notes')->value('field_value'))->toBe('updated note');

    // Component attached with a version created via firstOrCreate()
    expect($issue->components()->count())->toBe(1);
    $attachedComponent = $issue->components()->first();
    expect($attachedComponent->id)->toBe($component->id);
    expect($attachedComponent->pivot->component_version_id)->not->toBeNull();

    // Environment created and scoped to the issue
    expect($issue->environments()->count())->toBe(1);
    $issueEnvironment = $issue->environments()->first();
    expect($issueEnvironment->environment_id)->toBe($environment->id);
    expect($issueEnvironment->created_by)->toBe('compliance-officer');
    expect($issueEnvironment->updated_by)->toBe('compliance-officer');

    // Jurisdiction approval nested under the environment
    expect($issueEnvironment->jurisdictions()->count())->toBe(1);
    $issueJurisdiction = $issueEnvironment->jurisdictions()->first();
    expect($issueJurisdiction->jurisdiction_id)->toBe($jurisdiction->id);
    expect($issueJurisdiction->approval_type_id)->toBe($approvalType->id);
    expect($issueJurisdiction->updated_by)->toBe('compliance-officer');

    // Document uploaded and attached
    expect($issue->documents()->count())->toBe(1);
    $document = $issue->documents()->first();
    expect($document->file_name)->toBe('impact-assessment.pdf');
    expect($document->storage_path)->not->toBeNull();
    Storage::disk('local')->assertExists($document->storage_path);
    expect(Storage::disk('local')->get($document->storage_path))->toBe('PDF-BYTES');

    // Audit trail: one history entry written before the mutation
    expect(IssueHistory::where('issue_id', $issue->id)->count())->toBe(1);
});

test('Issue::updateFields() soft-deletes a previously attached component when marked deleted', function () {
    seedEditableIssueFields();

    $user = User::factory()->create(['username' => 'release-manager']);
    $this->actingAs($user);

    $issue = Issue::factory()->create(['issue_ref' => 'CM-8002']);
    $component = Component::create(['name' => 'fraud-detection-worker']);
    $issue->components()->attach($component->id, ['component_version_id' => null]);

    $result = $issue->updateFields(null, [
        'id' => $issue->id,
        'requires_approval' => 'None',
        'fields' => [],
        'components' => [
            ['id' => $component->id, 'deleted' => true],
        ],
        'environments' => [],
        'files' => [],
        'files_to_remove' => [],
    ]);

    expect($result)->toBe(['has_updated' => true, 'error' => null]);
    expect($issue->components()->count())->toBe(0); // pivot soft-deleted, no longer visible
});

test('Issue::deleteIssue() writes an audit entry and soft-deletes the issue', function () {
    $user = User::factory()->create(['username' => 'compliance-officer']);
    $this->actingAs($user);

    $issue = Issue::factory()->create(['issue_ref' => 'CM-8003']);

    $result = $issue->deleteIssue(null, ['id' => (string) $issue->id]);

    expect($result)->toBe(['has_deleted' => true, 'error' => null]);
    expect(IssueHistory::where('issue_id', $issue->id)->count())->toBe(1);
    expect(Issue::withTrashed()->findOrFail($issue->id)->trashed())->toBeTrue();
});
