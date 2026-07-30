<?php

// AcmetoCasino Change Management - Release Model
// Groups Issues into a deployable release package, synchronized from Jira
// "Release" issues via JiraSyncIncremental::importRelease().

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\SoftDeletes;

class Release extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'release_ref',
        'jiraId',
        'requires_approval',
    ];

    protected function casts(): array
    {
        return [
            'requires_approval' => 'boolean',
        ];
    }

    /** @return HasMany<ReleaseField, $this> */
    public function fields(): HasMany
    {
        return $this->hasMany(ReleaseField::class);
    }

    /** @return HasMany<ReleaseHistory, $this> */
    public function history(): HasMany
    {
        return $this->hasMany(ReleaseHistory::class);
    }

    /** @return BelongsToMany<Issue, $this> */
    public function issues(): BelongsToMany
    {
        return $this
            ->belongsToMany(Issue::class, 'release_issues')
            ->whereNull('release_issues.deleted_at')
            ->withPivot(['link_type_description', 'deleted_at'])
            ->withTimestamps();
    }
}
