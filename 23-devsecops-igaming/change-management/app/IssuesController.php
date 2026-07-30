<?php

// AcmetoCasino Change Management - Issues Controller
// REST endpoint for creating new change management issues with field tracking

namespace App\Http\Controllers;

use App\Http\Requests\CreateIssueRequest;
use App\Models\Issue;
use App\Models\IssueField;
use Exception;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\DB;

class IssuesController extends Controller
{
    /**
     * Create a new change management issue.
     * Sets initial status to 'Open' and records the summary field.
     * All operations wrapped in a transaction for consistency.
     */
    public function create(CreateIssueRequest $request)
    {
        try {
            DB::beginTransaction();

            $issue = new Issue();
            $issue->issue_ref = $request->input('issue_ref');
            $issue->save();

            // Store summary as EAV field
            $field = new IssueField();
            $field->field_name = 'summary';
            $field->field_value = $request->input('title');
            $field->field_value_set_by = Auth::user()->username;
            $field->issue_id = $issue->id;
            $field->save();

            // Set initial status
            $field = new IssueField();
            $field->field_name = 'status';
            $field->field_value = 'Open';
            $field->field_value_set_by = Auth::user()->username;
            $field->issue_id = $issue->id;
            $field->save();

            DB::commit();

            return response()->json([
                'code' => 200,
                'errors' => [],
                'data' => ['id' => $issue->id],
                'messages' => ['Issue was added successfully']
            ], 200);
        } catch (Exception $e) {
            DB::rollBack();
            return response()->json([
                'code' => 422,
                'errors' => ['Error adding issue'],
                'data' => [],
                'messages' => []
            ], 422);
        }
    }
}
