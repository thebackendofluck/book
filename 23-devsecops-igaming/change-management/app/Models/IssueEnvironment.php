<?php

// AcmetoCasino Change Management - IssueEnvironment Model
// Scopes an Issue to one deployment Environment (e.g. "this change goes to
// Production"), and carries the per-environment jurisdiction approvals
// (IssueJurisdiction). Created/updated by Issue::updateFields().

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\SoftDeletes;

class IssueEnvironment extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'issue_id',
        'environment_id',
        'go_live_date',
        'notes',
    ];

    protected function casts(): array
    {
        return [
            'go_live_date' => 'date',
        ];
    }

    /** @return BelongsTo<Issue, $this> */
    public function issue(): BelongsTo
    {
        return $this->belongsTo(Issue::class);
    }

    /** @return BelongsTo<Environment, $this> */
    public function environment(): BelongsTo
    {
        return $this->belongsTo(Environment::class);
    }

    /** @return HasMany<IssueJurisdiction, $this> */
    public function jurisdictions(): HasMany
    {
        return $this->hasMany(IssueJurisdiction::class);
    }
}
