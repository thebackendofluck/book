<?php

use Illuminate\Support\Facades\Route;

// Harness-only status page: the test rig has no frontend views, so the root
// route reports what the rig exposes instead of rendering a Blade template.
Route::get('/', function () {
    return response()->json([
        'app' => 'chapter-23 change-management e2e harness',
        'health' => '/up',
        'api' => [
            'POST /api/login' => 'harness-only session login {username}',
            'POST /api/issues' => 'create issue {issue_ref, title} (auth)',
            'POST /api/components' => 'register component {name, status, version?} (auth)',
        ],
    ]);
});
