# Files (core)

## What it owns

- Uploaded files: the bytes on local disk under the configured files root, and their metadata in Postgres.
- Accepted types are PDF, PNG, JPEG and plain text.
  The type is worked out from the bytes, never from the claimed type.
- Access: the first uploader may delete a file, and a person with `files.view` may read one.
- Schema: `files`, table `file`.

## Interface

Import from `app.core.files` only: `FileService` and `get_file_service`, which save, read, delete, check and erase files.
It also exports `FileRepository`, `router`, `ROLES` and `PERMISSIONS`.

## Key decisions

- Files are stored by SHA-256, so identical uploads share one blob.
  Storage keys are hashes, so file names never reach the disk path.
- A blob is written to a `.partial` file and then renamed, so a reader never sees half a file.
- Delete is gated for agents by the confirmation gate, and only the first uploader may delete.
- Each upload and delete writes its audit row in the same transaction as the change.
