<?php

// AcmetoCasino Change Management - Environment Model
// A deployment environment (e.g. "Production", "Staging", "UAT") that an Issue can
// be scoped to via IssueEnvironment. Used by Issue::scopeWhereEnvironmentNameLike().

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Environment extends Model
{
    use HasFactory;

    protected $fillable = [
        'name',
    ];

    /** @return HasMany<IssueEnvironment, $this> */
    public function issueEnvironments(): HasMany
    {
        return $this->hasMany(IssueEnvironment::class);
    }
}
