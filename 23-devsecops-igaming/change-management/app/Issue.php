<?php

// AcmetoCasino Change Management - Issue Model
// Eloquent model for tracking software changes with jurisdiction-aware filtering,
// environment scoping, and EAV (Entity-Attribute-Value) field pattern

namespace App\Models;

use Exception;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\SoftDeletes;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\DB;

class Issue extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'issue_ref',
        'jiraId',
        'requires_approval',
    ];

    /**
     * Dynamic field filtering for the issues list.
     * Supports environment names, jurisdiction codes, issue references,
     * and EAV field values (including date range queries).
     */
    public function fieldFilter($builder, $value)
    {
        $filters = json_decode(urldecode($value));

        foreach ($filters as $filter) {
            if ($filter->colName == "environment_names") {
                $builder = $builder->whereEnvironmentNameLike("%" . $filter->value . "%");
            } elseif ($filter->colName == "jurisdiction_names") {
                $builder = $builder->whereJurisdictionNameLike("%" . $filter->value . "%");
            } elseif ($filter->colName == "issue_ref") {
                $builder = $builder->where('issue_ref', "ILIKE", "%" . $filter->value . "%");
            } else {
                $builder = $builder->whereIn("id", function ($query) use ($filter) {
                    if (property_exists($filter, "isDate") && $filter->isDate) {
                        $dateParts = explode("#", $filter->value);
                        $query->select("issue_id")
                            ->from('issue_fields')
                            ->where('field_name', "=", $filter->colName)
                            ->whereRaw("field_value <> '' AND to_date(field_value,'YYYY-MM-DD') BETWEEN ? AND ?", $dateParts);
                    } else {
                        $query->select("issue_id")
                            ->from('issue_fields')
                            ->where('field_name', "=", $filter->colName)
                            ->where('field_value', "ILIKE", "%" . $filter->value . "%");
                    }
                });
            }
        }
        return $builder;
    }

    // Scope: issues not in Closed status
    public function scopeUnclosed($query)
    {
        return $query->whereNotIn("id", function ($query) {
            $query->select("issue_id")
                ->from('issue_fields')
                ->where('field_name', "=", 'cm_status')
                ->whereIn('field_value', ['Closed - Complete', 'Closed - Cancellled']);
        });
    }

    // Scope: only closed issues
    public function scopeClosed($query)
    {
        return $query->whereIn("id", function ($query) {
            $query->select("issue_id")
                ->from('issue_fields')
                ->where('field_name', "=", 'cm_status')
                ->whereIn('field_value', ['Closed - Complete', 'Closed - Cancellled']);
        });
    }

    // Scope: exclude cancelled issues
    public function scopeUncancelled($query)
    {
        return $query->whereIn("id", function ($query) {
            $query->select("issue_id")
                ->from('issue_fields')
                ->where('field_name', "=", 'status')
                ->whereNotIn('field_value', ['Cancelled', 'cancelled']);
        });
    }

    // Scope: filter by environment name
    public function scopeWhereEnvironmentNameLike($query, $searchString)
    {
        return $query->whereHas('environments.environment', function ($query) use ($searchString) {
            $query->where('environments.name', 'ilike', $searchString);
        });
    }

    // Scope: filter by jurisdiction code (e.g., UKGC, MGA, DGE)
    public function scopeWhereJurisdictionNameLike($query, $searchString)
    {
        return $query->whereHas('environments.jurisdictions.jurisdiction', function ($query) use ($searchString) {
            $query->where('jurisdictions.code', 'ilike', $searchString);
        });
    }

    // Subquery: aggregate environment names for list display
    public function scopeWithEnvironmentNames($query)
    {
        $query->addSelect(['environment_names' =>
            IssueEnvironment::join('environments', 'environments.id', '=', 'issue_environments.environment_id')
                ->select(DB::raw("string_agg(environments.name, ', ')"))
                ->whereColumn('issue_environments.issue_id', 'issues.id')
                ->take(1)
        ]);
    }

    // Subquery: aggregate jurisdiction codes for list display
    public function scopeWithJurisdictionNames($query)
    {
        $query->addSelect(['jurisdiction_names' =>
            IssueJurisdiction::join('jurisdictions', 'jurisdictions.id', '=', 'issue_jurisdictions.jurisdiction_id')
                ->select(DB::raw("string_agg(distinct(jurisdictions.code), ', ')"))
                ->whereColumn('issue_jurisdictions.issue_id', 'issues.id')
                ->take(1)
        ]);
    }

    // --- Relationships ---

    /** @return HasMany<IssueField, $this> */
    public function fields(): HasMany
    {
        return $this->hasMany(IssueField::class);
    }

    /** @return HasMany<IssueHistory, $this> */
    public function history(): HasMany
    {
        return $this->hasMany(IssueHistory::class);
    }

    /** @return HasMany<IssueField, $this> */
    public function listFields(): HasMany
    {
        return $this->hasMany(IssueField::class)->whereIn('field_name', function ($query) {
            $query->select("field_name")->from('field_info')->where('table', "=", "issues")->where('show_in_list', "=", true);
        })->select(array("field_value", "field_name"));
    }

    /** @return BelongsToMany<Component, $this> */
    public function components(): BelongsToMany
    {
        return $this
            ->belongsToMany(Component::class, 'issue_components')
            ->whereNull('issue_components.deleted_at')
            ->withPivot(['component_version_id', 'deleted_at'])
            ->withTimestamps();
    }

    /** @return BelongsToMany<ComponentVersion, $this> */
    public function componentVersions(): BelongsToMany
    {
        return $this
            ->belongsToMany(ComponentVersion::class, 'issue_components')
            ->whereNull('issue_components.deleted_at')
            ->withPivot(['component_version_id', 'deleted_at'])
            ->withTimestamps();
    }

    /** @return HasMany<IssueEnvironment, $this> */
    public function environments(): HasMany
    {
        return $this->hasMany(IssueEnvironment::class);
    }

    /** @return BelongsToMany<Release, $this> */
    public function releases(): BelongsToMany
    {
        return $this->belongsToMany(Release::class, 'release_issues');
    }

    /** @return BelongsToMany<Document, $this> */
    public function documents(): BelongsToMany
    {
        return $this
            ->belongsToMany(Document::class, 'issue_documents')
            ->whereNull('issue_documents.deleted_at')
            ->withTimestamps();
    }

    /**
     * Update issue fields, components, environments, jurisdictions, and documents
     * within a single database transaction. Creates audit history before changes.
     */
    public function updateFields($rootValue, array $args)
    {
        $user = Auth::user();

        return DB::transaction(function () use ($args, $user) {
            $id = (int) $args['id'];
            $requires_approval = $args['requires_approval'];
            $fields_new_vals = $args['fields'];
            $components_new_vals = $args['components'];
            $environments_new_vals = $args['environments'];
            $new_files = $args['files'];
            $files_to_remove = $args['files_to_remove'];

            $issue = $this->query()
                ->with('fields')
                ->with('components')
                ->with('documents')
                ->with('environments.jurisdictions')
                ->find($id);

            // Create audit trail before changes
            $history = new IssueHistory();
            $history->issue_id = $id;
            $history->details = json_encode([
                "changed_by" => $user,
                "data" => $issue
            ]);
            $history->save();

            $fieldAvailableToUpdate = FieldInfo::where("table", "=", "issues")
                ->where("can_edit", "=", true)->get();

            $getField = function ($field_name) use ($fieldAvailableToUpdate) {
                foreach ($fieldAvailableToUpdate as $field) {
                    if ($field_name == $field->field_name) return $field;
                }
            };

            $getIssueField = function ($field_name) use ($issue) {
                foreach ($issue->fields as $field) {
                    if ($field_name == $field->field_name) return $field;
                }
            };

            $issue['requires_approval'] = $requires_approval;

            // Update EAV fields
            foreach ($fields_new_vals as $fieldNewVal) {
                $field = $getField($fieldNewVal['field_name']);
                if (!$field) {
                    return ["has_updated" => false, "error" => "field '" . $fieldNewVal['field_name'] . "' cannot be updated"];
                }

                if ($field->is_base_table_field) {
                    if ($issue[$fieldNewVal['field_name']] != $fieldNewVal['value']) {
                        $issue[$fieldNewVal['field_name']] = $fieldNewVal['value'];
                    }
                } else {
                    $issueField = $getIssueField($fieldNewVal['field_name']);
                    if ($issueField) {
                        if ($field && $issueField->field_value != $fieldNewVal['value'] || $issueField->value_locked != $fieldNewVal['locked']) {
                            $issueField->field_value = $fieldNewVal['value'];
                            $issueField->field_value_set_by = $user->username;
                            $issueField->value_locked = ($fieldNewVal['locked'] === true || $fieldNewVal['locked'] === false)
                                ? $fieldNewVal['locked'] : true;
                            $issueField->update();
                        }
                    } else {
                        $issueField = new IssueField();
                        $issueField->issue_id = $id;
                        $issueField->field_name = $fieldNewVal['field_name'];
                        $issueField->field_value = $fieldNewVal['value'];
                        $issueField->field_value_set_by = $user->username;
                        $issueField->value_locked = ($fieldNewVal['value'] === true || $fieldNewVal['value'] === false)
                            ? $fieldNewVal['locked'] : true;
                        $issueField->save();
                    }
                }
            }

            // Soft-delete helper for pivot relationships
            $softDeletePivotItem = function ($relationship, $id, $description) {
                $item = $relationship->find($id);
                if ($item) {
                    $item->pivot->timestamps = false;
                    $item->pivot->update(['deleted_at' => $item->freshTimestamp()]);
                } else {
                    throw new Exception("Cannot find $description id " . $id);
                }
            };

            // Update component versions
            foreach ($components_new_vals as $component) {
                $componentVersionId = null;
                if (isset($component['componentVersionId'])) {
                    $componentVersionId = $component['componentVersionId'];
                } else if (isset($component['version']) || isset($component['checksum'])) {
                    $componentVersion = \App\Models\ComponentVersion::firstOrCreate(
                        ['component_id' => $component['id'], 'version' => $component['version'] ?? "", 'checksum' => $component['checksum'] ?? ""],
                        ['released' => isset($component['released']) ? new \DateTime($component['released']) : null]
                    );
                    $componentVersionId = $componentVersion->id;
                }

                if ($component['deleted'] ?? false) {
                    $softDeletePivotItem($issue->components(), $component['id'], 'component');
                } else {
                    $comp = $issue->components()->find($component['id']);
                    if ($comp) {
                        $comp->pivot->update(['component_version_id' => $componentVersionId]);
                    } else {
                        $issue->components()->attach($component['id'], ['component_version_id' => $componentVersionId]);
                    }
                }
            }

            // Update environment and jurisdiction approvals
            foreach ($environments_new_vals as $environmentNewVal) {
                if ($environmentNewVal['deleted'] ?? false) {
                    if ($environmentNewVal['id'] ?? false) {
                        $issueEnvironment = $issue->environments->find($environmentNewVal['id']);
                        if ($issueEnvironment) {
                            $issueEnvironment->updated_by = $user->username;
                            $issueEnvironment->deleted_at = new Carbon;
                            $issueEnvironment->save();
                        }
                    }
                } else {
                    if ($environmentNewVal['id'] ?? false) {
                        $issueEnvironment = $issue->environments->find($environmentNewVal['id']);
                        if (!$issueEnvironment) {
                            throw new Exception("Cannot find issue environment with id " . $environmentNewVal['id']);
                        }
                        $issueEnvironment->fill($environmentNewVal);
                        if ($issueEnvironment->isDirty()) {
                            $issueEnvironment->updated_by = $user->username;
                            $issueEnvironment->save();
                        }
                    } else {
                        $issueEnvironment = new \App\Models\IssueEnvironment($environmentNewVal);
                        $issueEnvironment->created_by = $user->username;
                        $issueEnvironment->updated_by = $user->username;
                        $issue->environments()->save($issueEnvironment);
                    }

                    // Handle jurisdiction-level approvals within each environment
                    foreach ($environmentNewVal['jurisdictions'] ?? [] as $jurisdictionNewVal) {
                        if ($jurisdictionNewVal['deleted'] ?? false) {
                            if ($jurisdictionNewVal['id'] ?? false) {
                                $issueJurisdiction = $issueEnvironment->jurisdictions->find($jurisdictionNewVal['id']);
                                if ($issueJurisdiction) {
                                    $issueJurisdiction->updated_by = $user->username;
                                    $issueJurisdiction->deleted_at = new Carbon;
                                    $issueJurisdiction->save();
                                }
                            }
                        } else {
                            if ($jurisdictionNewVal['id'] ?? false) {
                                $issueJurisdiction = $issueEnvironment->jurisdictions->find($jurisdictionNewVal['id']);
                                if (!$issueJurisdiction) {
                                    throw new Exception("Cannot find issue jurisdiction with id " . $jurisdictionNewVal['id']);
                                }
                                $issueJurisdiction->fill($jurisdictionNewVal);
                                if ($issueJurisdiction->isDirty()) {
                                    $issueJurisdiction->updated_by = $user->username;
                                    $issueJurisdiction->save();
                                }
                            } else {
                                $issueJurisdiction = new \App\Models\IssueJurisdiction([
                                    'jurisdiction_id' => $jurisdictionNewVal['jurisdiction_id'],
                                    'approval_type_id' => $jurisdictionNewVal['approval_type_id'],
                                    'approval_sent_on' => $jurisdictionNewVal['approval_sent_on'],
                                    'approval_received_on' => $jurisdictionNewVal['approval_received_on'],
                                    'updated_by' => $user->username,
                                ]);
                                $issueEnvironment->jurisdictions()->save($issueJurisdiction);
                            }
                        }
                    }
                }
            }

            // Handle document removals and additions
            foreach ($files_to_remove as $fileToRemoveId) {
                $softDeletePivotItem($issue->documents(), $fileToRemoveId, 'document');
            }

            foreach ($new_files as $new_file) {
                $document = new Document();
                $document->uploadDocument($new_file);
                $issue->documents()->save($document);
            }

            $issue->save();

            return ["has_updated" => true, "error" => null];
        });
    }

    public function deleteIssue($rootValue, array $args)
    {
        $user = Auth::user();
        $issue = Issue::findOrFail((int) $args['id']);

        return DB::transaction(function () use ($user, $issue) {
            $history = new IssueHistory();
            $history->issue_id = $issue->id;
            $history->details = json_encode([
                "changed_by" => $user,
                "data" => $issue
            ]);
            $history->save();

            $issue->delete();

            return ["has_deleted" => true, "error" => null];
        });
    }
}
