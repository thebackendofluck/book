<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Harness-only patch migration.
 *
 * `component_versions.released` is a required (non-nullable) `dateTime` column in
 * create_base_tables.php, but ComponentsController::create() never sets
 * $version->released when creating the initial version row. This is a pre-existing
 * gap in the sample (present since the original Laravel 8/10 version — not a
 * Laravel 12 regression); it currently causes a NOT NULL constraint failure that the
 * controller's catch(Exception) block silently turns into a 422 response. Patched
 * harness-only (nullable) so ComponentsController's happy path is exercisable without
 * altering create_base_tables.php's schema, which Part A requires to stay identical.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('component_versions', function (Blueprint $table) {
            $table->dateTime('released')->nullable()->change();
        });
    }

    public function down(): void
    {
        Schema::table('component_versions', function (Blueprint $table) {
            $table->dateTime('released')->nullable(false)->change();
        });
    }
};
