<?php

use App\Models\Component;
use App\Models\User;

test('POST /api/components registers a component and its initial version', function () {
    $user = User::factory()->create(['username' => 'release-manager']);

    $response = $this->actingAs($user)->postJson('/api/components', [
        'name' => 'payments-gateway-service',
        'status' => 'Active',
        'version' => '2.4.0',
    ]);

    $response->assertOk();
    $response->assertJson([
        'code' => 200,
        'messages' => ['Component was added successfully'],
    ]);

    $component = Component::where('name', 'payments-gateway-service')->firstOrFail();

    expect($component->fields()->where('field_name', 'status')->value('field_value'))
        ->toBe('Active');
    expect($component->fields()->where('field_name', 'status')->value('field_value_set_by'))
        ->toBe('release-manager');

    $version = $component->versions()->first();
    expect($version)->not->toBeNull();
    expect($version->version)->toBe('2.4.0');
    expect($version->checksum)->toBe('');
});

test('POST /api/components without a version skips creating a component_versions row', function () {
    $user = User::factory()->create(['username' => 'release-manager']);

    $this->actingAs($user)->postJson('/api/components', [
        'name' => 'fraud-detection-worker',
        'status' => 'Active',
    ])->assertOk();

    $component = Component::where('name', 'fraud-detection-worker')->firstOrFail();

    expect($component->versions()->count())->toBe(0);
});
