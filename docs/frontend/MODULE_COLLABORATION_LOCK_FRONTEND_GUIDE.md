# Module Collaboration Lock — Frontend Integration

These endpoints let the course creator freeze a module while collaborators
continue to view it. The freeze remains active until the creator explicitly
unlocks the module.

## Endpoints

```http
POST /api/v1/courses/{course_id}/modules/{module_id}/collaboration-lock/
POST /api/v1/courses/{course_id}/modules/{module_id}/collaboration-unlock/
```

The backend route names the path parameters `course_pk` and `pk`, but the
values are the course UUID and module UUID respectively.

Both requests require:

```http
Authorization: Bearer <access-token>
```

The request body is empty. Send `Content-Type: application/json` only if it is
required by the shared HTTP client.

## Lock a Module

```http
POST /api/v1/courses/{course_id}/modules/{module_id}/collaboration-lock/
```

Only the course creator can lock a module. The parent course must be in `DRAFT`
status. The operation is idempotent, so locking an already persistently locked
module returns the current locked module state.

## Unlock a Module

```http
POST /api/v1/courses/{course_id}/modules/{module_id}/collaboration-unlock/
```

Only the course creator can unlock a module. The parent course must be in
`DRAFT` status. Unlocking an already unlocked module is safe.

## Response

Both endpoints return the module representation. Use these fields to update
the module editor state:

```json
{
  "id": "module-uuid",
  "collaboration_locked": true,
  "collaboration_locked_at": "2026-09-18T10:30:00Z",
  "collaboration_locked_by": "creator-uuid",
  "is_locked": false,
  "locked_by": null,
  "lock_expires_at": null
}
```

`collaboration_locked` is the persistent creator freeze. When it is `true`,
`collaboration_locked_at` and `collaboration_locked_by` identify the lock. The
fields are `null` when the module is unlocked.

`is_locked`, `locked_by`, and `lock_expires_at` describe the separate,
short-lived editor lease. That lease expires and uses the existing `lock`,
`unlock`, and `heartbeat` endpoints; it is not a replacement for the
collaboration lock.

## Collaborator Behavior

While `collaboration_locked` is `true`:

- Collaborators can continue to load and view the module.
- Collaborator writes to the module return `423 Locked`.
- Lesson, assessment, reorder, delete, and lesson content writes under the
  module also return `423 Locked`.
- The course creator can continue to edit the module.

When a collaborator receives `423`, keep the current data visible and refresh
the module state so the UI can show that editing is frozen by the creator.

## Suggested UI Flow

1. Show `Lock module` when `collaboration_locked` is `false`.
2. Call the lock endpoint when the creator confirms the action.
3. Replace the action with `Unlock module` after a successful response.
4. Display a read-only state to collaborators when `collaboration_locked` is
   `true`.
5. Call the unlock endpoint when the creator chooses to resume collaboration.
6. Use the response body from either request as the authoritative module state.

Do not use a heartbeat for this persistent lock. The creator must explicitly
unlock it.

## Error Handling

| Status | Meaning |
|---|---|
| `200` | Lock or unlock succeeded; use the returned module state. |
| `400` | The course is not in `DRAFT` status. |
| `401` | The access token is missing or invalid. |
| `403` | The caller is not the course creator. |
| `404` | The course or module is not accessible to the caller. |
| `423` | A collaborator attempted a write while the module is locked. |

Example collaborator write error:

```json
{
  "errors": [
    {
      "type": "client_error",
      "code": "locked",
      "message": "This module is locked by the course creator for collaborator editing."
    }
  ]
}
```

