<?php

// AcmetoCasino Change Management - ComponentVersion Model
// A single version + checksum of a Component, used for deployment integrity
// verification and linked to Issues via the issue_components pivot.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class ComponentVersion extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'component_id',
        'version',
        'checksum',
        'released',
    ];

    protected function casts(): array
    {
        return [
            'released' => 'datetime',
        ];
    }

    /** @return BelongsTo<Component, $this> */
    public function component(): BelongsTo
    {
        return $this->belongsTo(Component::class);
    }
}
