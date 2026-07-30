<?php

use App\Models\Issue;
use App\Models\User;

test('POST /api/issues creates an issue with its transactional EAV fields', function () {
    $user = User::factory()->create(['username' => 'compliance-officer']);

    $response = $this->actingAs($user)->postJson('/api/issues', [
        'issue_ref' => 'CM-1001',
        'title' => 'Enable new payment provider in DE',
    ]);

    $response->assertOk();
    $response->assertJson([
        'code' => 200,
        'errors' => [],
        'messages' => ['Issue was added successfully'],
    ]);

    $issueId = $response->json('data.id');
    expect($issueId)->not->toBeNull();

    $issue = Issue::findOrFail($issueId);
    expect($issue->issue_ref)->toBe('CM-1001');

    // Both EAV fields written inside the DB::beginTransaction()/commit() block.
    expect($issue->fields()->where('field_name', 'summary')->value('field_value'))
        ->toBe('Enable new payment provider in DE');
    expect($issue->fields()->where('field_name', 'status')->value('field_value'))
        ->toBe('Open');

    foreach ($issue->fields as $field) {
        expect($field->field_value_set_by)->toBe('compliance-officer');
    }
});

test('POST /api/issues rolls back the transaction on failure', function () {
    $user = User::factory()->create(['username' => 'compliance-officer']);

    // Duplicate issue_ref violates the unique constraint on the second insert path
    // exercised by re-submitting the same ref twice; the second call must roll back
    // cleanly and return a 422 without leaving a duplicate issue behind.
    $this->actingAs($user)->postJson('/api/issues', [
        'issue_ref' => 'CM-DUP-1',
        'title' => 'First submission',
    ])->assertOk();

    $response = $this->actingAs($user)->postJson('/api/issues', [
        'issue_ref' => 'CM-DUP-1',
        'title' => 'Second submission with a duplicate ref',
    ]);

    $response->assertStatus(422);
    $response->assertJson([
        'code' => 422,
        'errors' => ['Error adding issue'],
    ]);

    expect(Issue::where('issue_ref', 'CM-DUP-1')->count())->toBe(1);
});
