<?php

namespace App\Models;

use Database\Factories\UserFactory;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Foundation\Auth\User as Authenticatable;

/**
 * Harness User model matching the chapter-23 `users` table schema
 * (id, username, last_login) defined by create_base_tables.php, rather than
 * the Laravel default (name/email/password). Used only to satisfy
 * Auth::user()->username calls in IssuesController/ComponentsController.
 */
class User extends Authenticatable
{
    /** @use HasFactory<UserFactory> */
    use HasFactory;

    /**
     * The `users` table (create_base_tables.php) has no created_at/updated_at
     * columns, unlike Laravel's default users schema.
     */
    public $timestamps = false;

    /**
     * The attributes that are mass assignable.
     *
     * @var list<string>
     */
    protected $fillable = [
        'username',
        'last_login',
    ];

    /**
     * Get the attributes that should be cast.
     *
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'last_login' => 'datetime',
        ];
    }
}
