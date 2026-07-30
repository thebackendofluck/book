<?php

// AcmetoCasino Change Management - Project Model
// A Jira project configured for synchronization by JiraSyncIncremental. Only
// projects that have at least one synchronizedIssueTypes entry are pulled
// (see JiraSyncIncremental::getProjects()).

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Project extends Model
{
    use HasFactory;

    protected $fillable = [
        'jira_id',
        'name',
    ];

    /** @return HasMany<ProjectIssueType, $this> */
    public function synchronizedIssueTypes(): HasMany
    {
        return $this->hasMany(ProjectIssueType::class);
    }
}
