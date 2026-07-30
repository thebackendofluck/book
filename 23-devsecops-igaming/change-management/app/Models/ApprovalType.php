<?php

// AcmetoCasino Change Management - ApprovalType Model
// The kind of regulatory approval required for a jurisdiction on an issue
// environment (e.g. "Regulatory Notification", "Regulatory Approval Required"),
// referenced by IssueJurisdiction::approval_type_id.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class ApprovalType extends Model
{
    use HasFactory;

    protected $fillable = [
        'name',
    ];

    /** @return HasMany<IssueJurisdiction, $this> */
    public function issueJurisdictions(): HasMany
    {
        return $this->hasMany(IssueJurisdiction::class);
    }
}
