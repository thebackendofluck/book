<?php

use App\Models\Issue;

test('Issue::unclosed() excludes issues whose cm_status EAV field is a closed status', function () {
    $open = Issue::factory()->create(['issue_ref' => 'CM-OPEN-1']);
    $open->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'In Progress',
        'field_value_set_by' => 'seed',
    ]);

    $closedComplete = Issue::factory()->create(['issue_ref' => 'CM-CLOSED-1']);
    $closedComplete->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'Closed - Complete',
        'field_value_set_by' => 'seed',
    ]);

    $closedCancelled = Issue::factory()->create(['issue_ref' => 'CM-CLOSED-2']);
    $closedCancelled->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'Closed - Cancellled', // matches the (typo'd) value in the sample scope
        'field_value_set_by' => 'seed',
    ]);

    $noStatusField = Issue::factory()->create(['issue_ref' => 'CM-NO-STATUS']);

    $unclosedRefs = Issue::unclosed()->pluck('issue_ref')->sort()->values()->all();

    expect($unclosedRefs)->toBe(['CM-NO-STATUS', 'CM-OPEN-1']);
});

test('Issue::closed() returns only issues in a closed cm_status', function () {
    $open = Issue::factory()->create(['issue_ref' => 'CM-OPEN-2']);
    $open->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'In Progress',
        'field_value_set_by' => 'seed',
    ]);

    $closed = Issue::factory()->create(['issue_ref' => 'CM-CLOSED-3']);
    $closed->fields()->create([
        'field_name' => 'cm_status',
        'field_value' => 'Closed - Complete',
        'field_value_set_by' => 'seed',
    ]);

    expect(Issue::closed()->pluck('issue_ref')->all())->toBe(['CM-CLOSED-3']);
});
