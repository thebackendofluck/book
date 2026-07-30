<?php

// AcmetoCasino Change Management - FieldInfo Model
// Metadata table controlling which EAV/base-table fields exist per table (issues,
// releases, components), whether they're editable/filterable/shown in list views,
// and how they map to Jira fields for JiraSyncIncremental.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\SoftDeletes;

class FieldInfo extends Model
{
    use HasFactory;
    use SoftDeletes;

    // The migration creates a singular `field_info` table; Eloquent's default
    // pluralization would guess `field_infos`, so the table name is explicit.
    protected $table = 'field_info';

    protected $fillable = [
        'title',
        'field_name',
        'table',
        'is_base_table_field',
        'show_in_list',
        'can_edit',
        'can_filter',
        'select_values',
        'jira_name',
        'type',
        'order',
        'list_order',
    ];

    protected function casts(): array
    {
        return [
            'is_base_table_field' => 'boolean',
            'show_in_list' => 'boolean',
            'can_edit' => 'boolean',
            'can_filter' => 'boolean',
        ];
    }
}
