<?php

namespace App\Http\Requests;

use Illuminate\Foundation\Http\FormRequest;

/**
 * Harness-only support request. IssuesController::create(CreateIssueRequest $request)
 * type-hints this class but it isn't one of the 5 chapter-23 sample files, so it's
 * reconstructed here from the fields the controller actually reads
 * (issue_ref, title).
 */
class CreateIssueRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'issue_ref' => ['required', 'string', 'max:100'],
            'title' => ['required', 'string'],
        ];
    }
}
