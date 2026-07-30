<?php

// AcmetoCasino Change Management - IssueJurisdiction Model
// A single jurisdiction's regulatory approval status for one IssueEnvironment
// (e.g. "UKGC approval requested on 2026-07-01, received on 2026-07-05").
// Created/updated by Issue::updateFields().

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\SoftDeletes;

class IssueJurisdiction extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'issue_environment_id',
        'jurisdiction_id',
        'approval_type_id',
        'approval_sent_on',
        'approval_received_on',
        'updated_by',
    ];

    protected function casts(): array
    {
        return [
            'approval_sent_on' => 'date',
            'approval_received_on' => 'date',
        ];
    }

    /** @return BelongsTo<IssueEnvironment, $this> */
    public function issueEnvironment(): BelongsTo
    {
        return $this->belongsTo(IssueEnvironment::class);
    }

    /** @return BelongsTo<Jurisdiction, $this> */
    public function jurisdiction(): BelongsTo
    {
        return $this->belongsTo(Jurisdiction::class);
    }

    /** @return BelongsTo<ApprovalType, $this> */
    public function approvalType(): BelongsTo
    {
        return $this->belongsTo(ApprovalType::class);
    }
}
