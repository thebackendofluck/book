<?php

// AcmetoCasino Change Management - Components Controller
// REST endpoint for registering software components with version tracking

namespace App\Http\Controllers;

use App\Http\Requests\CreateComponentRequest;
use App\Models\Component;
use App\Models\ComponentField;
use App\Models\ComponentVersion;
use Exception;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\DB;

class ComponentsController extends Controller
{
    /**
     * Register a new software component in the change management system.
     * Optionally creates an initial version record.
     * Used to track what software artifacts are deployed per environment.
     */
    public function create(CreateComponentRequest $request)
    {
        try {
            DB::beginTransaction();

            $component = new Component();
            $component->name = $request->input('name');
            $component->save();

            // Store component status as EAV field
            $field = new ComponentField();
            $field->field_name = 'status';
            $field->field_value = $request->input('status');
            $field->field_value_set_by = Auth::user()->username;
            $field->component_id = $component->id;
            $field->save();

            // Create initial version if provided
            if ($request->filled('version')) {
                $version = new ComponentVersion();
                $version->checksum = '';
                $version->version = $request->input('version');
                $version->component_id = $component->id;
                $version->save();
            }

            DB::commit();

            return response()->json([
                'code' => 200,
                'errors' => [],
                'data' => [],
                'messages' => ['Component was added successfully']
            ], 200);
        } catch (Exception $e) {
            DB::rollBack();
            return response()->json([
                'code' => 422,
                'errors' => ['Error adding component'],
                'data' => [],
                'messages' => []
            ], 422);
        }
    }
}
