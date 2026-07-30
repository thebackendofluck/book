<?php

// AcmetoCasino Change Management - ProjectIssueType Model
// One Jira issue type (e.g. "Bug", "Release") that a Project is configured to
// synchronize. Used to build the JQL issuetype filter in
// JiraSyncIncremental::buildSearchJql().

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class ProjectIssueType extends Model
{
    use HasFactory;

    protected $fillable = [
        'project_id',
        'jira_id',
        'name',
    ];

    /** @return BelongsTo<Project, $this> */
    public function project(): BelongsTo
    {
        return $this->belongsTo(Project::class);
    }
}
