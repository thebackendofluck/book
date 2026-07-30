<?php

// AcmetoCasino Change Management - ComponentField Model
// EAV row for a single dynamic field on a Component (e.g. status).

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class ComponentField extends Model
{
    use SoftDeletes;

    protected $fillable = [
        'component_id',
        'field_name',
        'field_value',
        'field_value_set_by',
    ];

    /** @return BelongsTo<Component, $this> */
    public function component(): BelongsTo
    {
        return $this->belongsTo(Component::class);
    }
}
