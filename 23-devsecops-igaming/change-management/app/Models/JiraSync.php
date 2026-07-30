<?php

// AcmetoCasino Change Management - JiraSync Model
// Bookkeeping row tracking one Jira issue id discovered by
// JiraSyncIncremental::importIdsOfUpdatedIssueFromJira(), and whether it has
// since been synchronized into the local issues/releases tables.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class JiraSync extends Model
{
    use HasFactory;

    protected $table = 'jira_syncs';

    protected $fillable = [
        'jira_id',
        'issue_ref',
        'issue_updated',
        'synchronized',
    ];

    protected function casts(): array
    {
        return [
            'issue_updated' => 'datetime',
            'synchronized' => 'boolean',
        ];
    }
}
