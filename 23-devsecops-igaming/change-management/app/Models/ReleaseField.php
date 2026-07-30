<?php

// AcmetoCasino Change Management - ReleaseField Model
// EAV row for a single dynamic field on a Release, imported via
// JiraSyncIncremental::importReleaseFields().

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class ReleaseField extends Model
{
    use SoftDeletes;

    protected $fillable = [
        'release_id',
        'field_name',
        'field_value',
        'field_value_set_by',
        'value_locked',
    ];

    protected function casts(): array
    {
        return [
            'value_locked' => 'boolean',
        ];
    }

    /** @return BelongsTo<Release, $this> */
    public function release(): BelongsTo
    {
        return $this->belongsTo(Release::class);
    }
}
