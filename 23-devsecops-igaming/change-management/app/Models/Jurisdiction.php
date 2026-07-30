<?php

// AcmetoCasino Change Management - Jurisdiction Model
// A regulatory jurisdiction (e.g. UKGC, MGA, DGE) that an issue's environment
// approvals are tracked against via IssueJurisdiction. Used by
// Issue::scopeWhereJurisdictionNameLike().

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Jurisdiction extends Model
{
    use HasFactory;

    protected $fillable = [
        'code',
        'name',
    ];

    /** @return HasMany<IssueJurisdiction, $this> */
    public function issueJurisdictions(): HasMany
    {
        return $this->hasMany(IssueJurisdiction::class);
    }
}
