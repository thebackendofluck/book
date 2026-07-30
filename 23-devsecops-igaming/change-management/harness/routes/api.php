<?php

use App\Http\Controllers\ComponentsController;
use App\Http\Controllers\IssuesController;
use App\Models\User;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\Route;

// Harness-only login: the chapter-23 users table (id, username, last_login) has
// no password column, so the rig authenticates by username alone to let curl and
// browser runs exercise the same session 'auth' guard the Pest tests drive with
// actingAs(). Never a pattern for production auth.
Route::post('/login', function (Request $request) {
    abort_if(app()->isProduction(), 404);

    $validated = $request->validate(['username' => ['required', 'string', 'max:100']]);

    // last_login is NOT NULL in the chapter-23 users schema, so it must be set
    // on first creation, not only on the follow-up touch.
    $user = User::firstOrCreate(['username' => $validated['username']], ['last_login' => now()]);
    $user->update(['last_login' => now()]);
    Auth::login($user);

    return response()->json(['authenticated' => true, 'username' => $user->username]);
});

Route::middleware('auth')->group(function () {
    Route::post('/issues', [IssuesController::class, 'create']);
    Route::post('/components', [ComponentsController::class, 'create']);
});
