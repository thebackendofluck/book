<?php

use App\Models\Document;
use Illuminate\Support\Facades\Storage;

test('Document::uploadDocument stores the file contents on disk and records the path', function () {
    Storage::fake('local');

    $document = new Document();
    $document->uploadDocument([
        'file_name' => 'report.csv',
        'file_type' => 'text/csv',
        'description' => 'Compliance export',
        'contents' => "a,b,c\n1,2,3",
    ]);
    $document->save();

    expect($document->file_name)->toBe('report.csv');
    expect($document->file_type)->toBe('text/csv');
    expect($document->description)->toBe('Compliance export');
    expect($document->storage_path)->not->toBeNull();
    expect($document->storage_path)->toContain('report.csv');

    Storage::disk('local')->assertExists($document->storage_path);
    expect(Storage::disk('local')->get($document->storage_path))->toBe("a,b,c\n1,2,3");
});

test('Document::uploadDocument defaults the file type and skips storage when no contents are given', function () {
    Storage::fake('local');

    $document = new Document();
    $document->uploadDocument([
        'file_name' => 'notes.txt',
        'description' => 'no content provided',
    ]);
    $document->save();

    expect($document->file_type)->toBe('application/octet-stream');
    expect($document->storage_path)->toBeNull();
});
