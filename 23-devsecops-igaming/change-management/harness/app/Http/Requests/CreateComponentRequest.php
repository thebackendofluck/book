<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

/**
 * Harness-only support request. ComponentsController::create(CreateComponentRequest
 * $request) type-hints this class but it isn't one of the 5 chapter-23 sample files,
 * so it's reconstructed here from the fields the controller actually reads
 * (name, status, version).
 */
class CreateComponentRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:2000'],
            'status' => ['required', 'string'],
            'version' => ['nullable', 'string'],
        ];
    }
}
