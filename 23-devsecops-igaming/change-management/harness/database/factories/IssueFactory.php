<?php

namespace Database\Factories;

use App\Models\Issue;
use Illuminate\Database\Eloquent\Factories\Factory;

/**
 * @extends Factory<Issue>
 */
class IssueFactory extends Factory
{
    protected $model = Issue::class;

    public function definition(): array
    {
        return [
            'issue_ref' => strtoupper(fake()->unique()->bothify('CM-####')),
            'requires_approval' => 'None',
        ];
    }
}
