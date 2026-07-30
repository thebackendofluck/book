<?php

// AcmetoCasino Change Management - Document Model
// A file attachment for an Issue or Release (e.g. a data protection impact
// assessment or a regulator approval letter). uploadDocument() stores the raw
// file contents on the configured "local" disk and records where it landed.

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsToMany;
use Illuminate\Database\Eloquent\SoftDeletes;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;

class Document extends Model
{
    use HasFactory;
    use SoftDeletes;

    protected $fillable = [
        'file_name',
        'file_type',
        'description',
        'storage_path',
    ];

    /** @return BelongsToMany<Issue, $this> */
    public function issues(): BelongsToMany
    {
        return $this
            ->belongsToMany(Issue::class, 'issue_documents')
            ->whereNull('issue_documents.deleted_at')
            ->withTimestamps();
    }

    /**
     * Persist the uploaded file's metadata and, when raw contents are supplied,
     * store them on the "local" disk under a collision-safe path.
     *
     * @param array{file_name: string, file_type?: string|null, description?: string|null, contents?: string|null} $fileData
     */
    public function uploadDocument(array $fileData): void
    {
        $this->file_name = $fileData['file_name'];
        $this->file_type = $fileData['file_type'] ?? 'application/octet-stream';
        $this->description = $fileData['description'] ?? '';

        if (!empty($fileData['contents'])) {
            $path = 'documents/' . Str::uuid()->toString() . '_' . $this->file_name;
            Storage::disk('local')->put($path, $fileData['contents']);
            $this->storage_path = $path;
        }
    }
}
