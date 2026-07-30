<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    // Auto-register Artisan commands from app/Console/Commands. JiraSyncIncremental is
    // registered explicitly by class name because it's symlinked in from the chapter-23
    // sample directory: Laravel's directory-based command discovery calls
    // SplFileInfo::getRealPath() on each file to derive its namespace, which resolves a
    // symlink to its target path (outside app/), breaking that calculation. Explicit
    // registration bypasses directory scanning and uses normal PSR-4 autoloading instead.
    ->withCommands([
        __DIR__.'/../app/Console/Commands',
        \App\Console\Commands\JiraSyncIncremental::class,
    ])
    ->withMiddleware(function (Middleware $middleware): void {
        // Harness-only: the sample's api routes are guarded by the session-based
        // 'auth' middleware (tests drive it with actingAs()). To exercise the same
        // guard over real HTTP, give the api group a session stack so POST
        // /api/login can establish an authenticated session for curl/browser runs.
        $middleware->api(append: [
            \Illuminate\Cookie\Middleware\EncryptCookies::class,
            \Illuminate\Cookie\Middleware\AddQueuedCookiesToResponse::class,
            \Illuminate\Session\Middleware\StartSession::class,
        ]);
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        //
    })->create();
