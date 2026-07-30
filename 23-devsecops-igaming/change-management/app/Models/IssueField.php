<?php

// AcmetoCasino Change Management - IssueField Model
// EAV (Entity-Attribute-Value) row for a single dynamic field on an Issue.
// Lets the field_info metadata table drive which fields exist without schema changes.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class IssueField extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'issue_id',
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

    /** @return BelongsTo<Issue, $this> */
    public function issue(): BelongsTo
    {
        return $this->belongsTo(Issue::class);
    }
}
