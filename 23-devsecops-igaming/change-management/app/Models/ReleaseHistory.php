<?php

// AcmetoCasino Change Management - ReleaseHistory Model
// Full audit trail row for a Release, written by
// JiraSyncIncremental::saveReleaseHistory() on every re-import.

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class ReleaseHistory extends Model
{
    use SoftDeletes;

    // The migration creates a singular `release_history` table; Eloquent's default
    // pluralization would guess `release_histories`, so the table name is explicit.
    protected $table = 'release_history';

    protected $fillable = [
        'release_id',
        'details',
    ];

    /** @return BelongsTo<Release, $this> */
    public function release(): BelongsTo
    {
        return $this->belongsTo(Release::class);
    }
}
