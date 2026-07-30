<?php

// AcmetoCasino Change Management - Base Database Schema
// Creates the full schema for issues, releases, components, approvals, and documents
// Uses soft deletes throughout for audit compliance

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('users', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->string('username', 255);
            $table->dateTime('last_login', 0);
        });

        // Issues: track software changes, bugs, feature requests
        Schema::create('issues', function (Blueprint $table) {
            $table->id();
            $table->string('issue_ref', 100)->unique();
            $table->string('jiraId')->nullable(); // Jira issue id, set by JiraSyncIncremental
            $table->string('requires_approval', 5)->default('None');
            $table->softDeletes();
            $table->timestamps();
        });

        // Issue fields: EAV pattern for flexible field storage
        // Allows Jira field mapping without schema changes
        Schema::create('issue_fields', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('issue_id');
            $table->string('field_name', 255);
            $table->text('field_value')->nullable();
            $table->string('field_value_set_by', 255);
            $table->boolean('value_locked')->default(false); // Locked = won't be overwritten by Jira sync
            $table->unique(['id', 'field_name']);
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('issue_id')->references('id')->on('issues');
        });

        // Releases: group issues into deployable release packages
        Schema::create('releases', function (Blueprint $table) {
            $table->id();
            $table->string('release_ref', 100)->unique();
            $table->string('jiraId')->nullable(); // Jira issue id, set by JiraSyncIncremental
            $table->boolean('requires_approval')->default(false);
            $table->softDeletes();
            $table->timestamps();
        });

        Schema::create('release_fields', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('release_id');
            $table->string('field_name', 255);
            $table->text('field_value')->nullable();
            $table->string('field_value_set_by', 255);
            $table->boolean('value_locked')->default(false);
            $table->unique(['release_id', 'field_name']);
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('release_id')->references('id')->on('releases');
        });

        // Many-to-many: issues linked to releases
        Schema::create('release_issues', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('release_id');
            $table->bigInteger('issue_id');
            $table->string('link_type_description')->nullable(); // Jira issue-link type, e.g. "Blocks"
            $table->softDeletes();
            $table->timestamps();
            $table->unique(['release_id', 'issue_id']);
            $table->foreign('release_id')->references('id')->on('releases');
            $table->foreign('issue_id')->references('id')->on('issues');
        });

        // Documents: file attachments for issues and releases
        Schema::create('documents', function (Blueprint $table) {
            $table->id();
            $table->string('file_name', 255);
            $table->string('file_type', 255);
            $table->string('description', 2000);
            $table->string('storage_path')->nullable(); // Set by Document::uploadDocument()
            $table->softDeletes();
            $table->timestamps();
        });

        // Approvers: people who can approve changes
        Schema::create('approvers', function (Blueprint $table) {
            $table->id();
            $table->string('name', 255);
            $table->softDeletes();
            $table->timestamps();
        });

        // Issue approvals: multi-level approval workflow
        Schema::create('issue_approvals', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('issue_id');
            $table->bigInteger('approver_id');
            $table->string('note', 2000)->nullable();
            $table->boolean('has_approved')->default(false);
            $table->dateTime('approved_on')->nullable()->default(null);
            $table->string('approval_ref')->default(false);
            $table->bigInteger('approval_document_id')->nullable();
            $table->unique(['issue_id', 'approver_id']);
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('issue_id')->references('id')->on('issues');
            $table->foreign('approver_id')->references('id')->on('approvers');
            $table->foreign('approval_document_id')->references('id')->on('documents');
        });

        Schema::create('issue_documents', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('issue_id');
            $table->bigInteger('document_id');
            $table->unique(['issue_id', 'document_id']);
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('issue_id')->references('id')->on('issues');
            $table->foreign('document_id')->references('id')->on('documents');
        });

        Schema::create('release_documents', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('release_id');
            $table->bigInteger('document_id');
            $table->unique(['release_id', 'document_id']);
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('release_id')->references('id')->on('releases');
            $table->foreign('document_id')->references('id')->on('documents');
        });

        // Components: software artifacts tracked for deployment
        Schema::create('components', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->string('name', 2000);
            $table->softDeletes();
            $table->timestamps();
        });

        // Component versions: version + checksum for integrity verification
        Schema::create('component_versions', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->bigInteger('component_id');
            $table->string('version', 2000);
            $table->string('checksum', 1024);
            $table->dateTime('released');
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('component_id')->references('id')->on('components');
        });

        Schema::create('component_fields', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('component_id');
            $table->string('field_name', 255);
            $table->text('field_value')->nullable();
            $table->string('field_value_set_by', 255);
            $table->unique(['component_id', 'field_name']);
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('component_id')->references('id')->on('components');
        });

        // History tables: full audit trail for compliance
        Schema::create('component_history', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('component_id');
            $table->text('details');
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('component_id')->references('id')->on('components');
        });

        Schema::create('issue_history', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('issue_id');
            $table->text('details');
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('issue_id')->references('id')->on('issues');
        });

        Schema::create('release_history', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('release_id');
            $table->text('details');
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('release_id')->references('id')->on('releases');
        });

        // Issue-component linking with version tracking
        Schema::create('issue_components', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('component_id');
            $table->bigInteger('component_version_id')->nullable();
            $table->bigInteger('issue_id');
            $table->softDeletes();
            $table->timestamps();
            $table->unique(['component_id', 'issue_id']);
            $table->foreign('component_id')->references('id')->on('components');
            $table->foreign('component_version_id')->references('id')->on('component_versions');
            $table->foreign('issue_id')->references('id')->on('issues');
        });

        // Field info: metadata table controlling which fields are displayed,
        // editable, filterable, and how they map to Jira fields
        Schema::create('field_info', function (Blueprint $table) {
            $table->id();
            $table->string('title', 255);
            $table->string('field_name', 255);
            $table->string('table', 255);
            $table->boolean('is_base_table_field');
            $table->boolean('show_in_list');
            $table->boolean('can_edit');
            $table->boolean('can_filter');
            $table->string('select_values')->nullable();
            $table->string('jira_name')->nullable();      // Maps to Jira field path (e.g., 'fields.status.name')
            $table->string('type', 255);                   // Field type: text, datetime, select, etc.
            $table->mediumInteger('order');
            $table->mediumInteger('list_order');
            $table->softDeletes();
            $table->timestamps();
        });

        // Environments: deployment environments an issue can be scoped to
        // (e.g. Production, Staging, UAT)
        Schema::create('environments', function (Blueprint $table) {
            $table->id();
            $table->string('name', 255)->unique();
            $table->timestamps();
        });

        // Jurisdictions: regulatory bodies whose approval an issue may need per
        // environment (e.g. UKGC, MGA, DGE)
        Schema::create('jurisdictions', function (Blueprint $table) {
            $table->id();
            $table->string('code', 50)->unique();
            $table->string('name', 255);
            $table->timestamps();
        });

        // Approval types: the kind of regulatory approval required
        // (e.g. Regulatory Notification, Regulatory Approval Required)
        Schema::create('approval_types', function (Blueprint $table) {
            $table->id();
            $table->string('name', 255)->unique();
            $table->timestamps();
        });

        // Issue environments: scopes an issue to one deployment environment
        Schema::create('issue_environments', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('issue_id');
            $table->bigInteger('environment_id');
            $table->date('go_live_date')->nullable();
            $table->string('notes', 2000)->nullable();
            $table->string('created_by', 255)->nullable();
            $table->string('updated_by', 255)->nullable();
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('issue_id')->references('id')->on('issues');
            $table->foreign('environment_id')->references('id')->on('environments');
        });

        // Issue jurisdictions: per-environment jurisdiction approval tracking
        Schema::create('issue_jurisdictions', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('issue_environment_id');
            $table->bigInteger('jurisdiction_id');
            $table->bigInteger('approval_type_id');
            $table->date('approval_sent_on')->nullable();
            $table->date('approval_received_on')->nullable();
            $table->string('updated_by', 255)->nullable();
            $table->softDeletes();
            $table->timestamps();
            $table->foreign('issue_environment_id')->references('id')->on('issue_environments');
            $table->foreign('jurisdiction_id')->references('id')->on('jurisdictions');
            $table->foreign('approval_type_id')->references('id')->on('approval_types');
        });

        // Projects: Jira projects configured for synchronization
        Schema::create('projects', function (Blueprint $table) {
            $table->id();
            $table->string('jira_id', 255)->unique();
            $table->string('name', 255);
            $table->timestamps();
        });

        // Project issue types: the Jira issue types synchronized per project
        Schema::create('project_issue_types', function (Blueprint $table) {
            $table->id();
            $table->bigInteger('project_id');
            $table->string('jira_id', 255);
            $table->string('name', 255);
            $table->timestamps();
            $table->foreign('project_id')->references('id')->on('projects');
        });

        // Jira syncs: bookkeeping of Jira issue ids discovered by the incremental
        // search and whether they've since been imported
        Schema::create('jira_syncs', function (Blueprint $table) {
            $table->id();
            $table->string('jira_id', 255);
            $table->string('issue_ref', 100);
            $table->dateTime('issue_updated');
            $table->boolean('synchronized')->default(false);
            $table->timestamps();
            $table->unique(['jira_id', 'issue_ref', 'issue_updated']);
        });
    }

    public function down(): void
    {
        // Reverse of up(): drop dependents before the tables they reference.
        Schema::dropIfExists('jira_syncs');
        Schema::dropIfExists('project_issue_types');
        Schema::dropIfExists('projects');
        Schema::dropIfExists('issue_jurisdictions');
        Schema::dropIfExists('issue_environments');
        Schema::dropIfExists('approval_types');
        Schema::dropIfExists('jurisdictions');
        Schema::dropIfExists('environments');
        Schema::dropIfExists('field_info');
        Schema::dropIfExists('issue_components');
        Schema::dropIfExists('release_history');
        Schema::dropIfExists('issue_history');
        Schema::dropIfExists('component_history');
        Schema::dropIfExists('component_fields');
        Schema::dropIfExists('component_versions');
        Schema::dropIfExists('components');
        Schema::dropIfExists('release_documents');
        Schema::dropIfExists('issue_documents');
        Schema::dropIfExists('issue_approvals');
        Schema::dropIfExists('approvers');
        Schema::dropIfExists('documents');
        Schema::dropIfExists('release_issues');
        Schema::dropIfExists('release_fields');
        Schema::dropIfExists('releases');
        Schema::dropIfExists('issue_fields');
        Schema::dropIfExists('issues');
        Schema::dropIfExists('users');
    }
};
