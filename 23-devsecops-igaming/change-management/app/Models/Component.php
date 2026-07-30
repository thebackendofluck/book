<?php

// AcmetoCasino Change Management - Component Model
// A software artifact tracked for deployment (e.g. a service or package),
// versioned via ComponentVersion and described via the ComponentField EAV table.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\SoftDeletes;

class Component extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'name',
    ];

    /** @return HasMany<ComponentVersion, $this> */
    public function versions(): HasMany
    {
        return $this->hasMany(ComponentVersion::class);
    }

    /** @return HasMany<ComponentField, $this> */
    public function fields(): HasMany
    {
        return $this->hasMany(ComponentField::class);
    }

    /** @return BelongsToMany<Issue, $this> */
    public function issues(): BelongsToMany
    {
        return $this
            ->belongsToMany(Issue::class, 'issue_components')
            ->whereNull('issue_components.deleted_at')
            ->withPivot(['component_version_id', 'deleted_at'])
            ->withTimestamps();
    }
}
