<?php

// AcmetoCasino Change Management - IssueHistory Model
// Full audit trail row for an Issue: a JSON snapshot taken before every mutation
// (manual edits via Issue::updateFields()/deleteIssue() and Jira re-imports via
// JiraSyncIncremental::saveIssueHistory()).

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class IssueHistory extends Model
{
    use SoftDeletes;

    // The migration creates a singular `issue_history` table; Eloquent's default
    // pluralization would guess `issue_histories`, so the table name is explicit.
    protected $table = 'issue_history';

    protected $fillable = [
        'issue_id',
        'details',
    ];

    /** @return BelongsTo<Issue, $this> */
    public function issue(): BelongsTo
    {
        return $this->belongsTo(Issue::class);
    }
}
