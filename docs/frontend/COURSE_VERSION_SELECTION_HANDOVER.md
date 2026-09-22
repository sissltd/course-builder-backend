# Course Version Selection Handover

## Purpose

`Course.version` is the publishing standard selected for a course. It is a
foreign key to a canonical `CourseVersion` record, not a free-text value and
not a client-defined label.

The creator may start a draft without selecting a version, but an active
version must be saved before the course is submitted for review.

## Endpoints

All endpoints are under `/api/v1/` and require a Bearer access token unless
stated otherwise.

| Purpose | Method | Endpoint |
|---|---:|---|
| Load selectable versions | `GET` | `/course-versions/` |
| Create a draft | `POST` | `/courses/` |
| Replace a draft | `PUT` | `/courses/{course_id}/` |
| Update draft fields | `PATCH` | `/courses/{course_id}/` |
| Submit for review | `POST` | `/courses/{course_id}/submit/` |

The version list is authenticated, unpaginated, ordered by label, and only
contains active versions. Do not hardcode version labels in the frontend.

## Recommended Creator Flow

1. Load the course draft and the selectable versions when the Versioning step
   opens.
2. Render the returned `label` values in the picker and keep the selected
   `id` as the form value.
3. Save the selected ID with `PATCH /courses/{course_id}/`.
4. Refresh the course detail and use `course.version` to display the saved
   selection.
5. Block or warn before calling the submit endpoint when no version is saved.
6. If submission returns the version validation error, focus the Versioning
   control rather than displaying the error as a generic structural-check
   failure.

## Load Versions

```http
GET /api/v1/course-versions/
Authorization: Bearer <access_token>
Accept: application/json
```

Example response:

```json
[
  {
    "id": "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b",
    "label": "1.0"
  },
  {
    "id": "8a6d2c10-7f4e-4c81-b952-1e3d5a7b9c20",
    "label": "2.0"
  }
]
```

An empty response means there are currently no active versions available.
The UI should show an unavailable state and prevent submission rather than
inventing a value.

## Create A Draft With A Version

`version` is optional during draft creation:

```http
POST /api/v1/courses/
Content-Type: application/json
Authorization: Bearer <access_token>
```

```json
{
  "category": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
  "title": "Intro to Python",
  "description": "A hands-on introduction to Python for beginners.",
  "difficulty_level": "BEGINNER",
  "learning_objectives": ["Write basic Python scripts"],
  "tags": ["python", "beginner"],
  "version": "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b",
  "duration_hours": 2,
  "terms_accepted": true
}
```

The ID must belong to an active `CourseVersion`. The API rejects inactive or
unknown IDs with a validation response.

## Update An Existing Draft

The normal builder save is a partial update:

```http
PATCH /api/v1/courses/{course_id}/
Content-Type: application/json
Authorization: Bearer <access_token>
```

```json
{
  "version": "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
}
```

The same field is accepted by the full replacement endpoint:

```http
PUT /api/v1/courses/{course_id}/
```

Use the selected version ID, not the label (`"1.0"`). The course must still
be in `DRAFT` for either update endpoint.

After saving, a course detail response includes the selected version as a
read-only object:

```json
{
  "version": {
    "id": "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b",
    "label": "1.0"
  }
}
```

## Submit For Review

```http
POST /api/v1/courses/{course_id}/submit/
Authorization: Bearer <access_token>
```

The submit request has no request body. Before calling it, confirm that the
course detail contains a non-null `version`.

If it is missing, the API returns a structural validation envelope similar to:

```json
{
  "errors": [
    {
      "type": "validation_error",
      "code": "invalid",
      "message": "Course version must be selected before submission.",
      "field_name": "structural_standards"
    }
  ]
}
```

Although the error is grouped under `structural_standards`, the corrective
action is to save `course.version`. It is not a field that should be added to
the structural-check form.

## UI Behavior

- Show a loading state while `GET /course-versions/` is in progress.
- Show each version's `label`; submit its `id`.
- Mark the control invalid when submission reports a missing version.
- Preserve the selected ID when navigating between builder steps.
- Re-fetch or invalidate the course detail after saving so the selected label
  is reflected everywhere.
- Do not show a hardcoded default when the API returns no active version.
- Do not allow a creator to edit a submitted, approved, or published course
  through the draft update endpoint.

## Swagger Documentation

Use either:

- `/api/v1/docs/` for the interactive Swagger UI
- `/api/schema/` for the raw OpenAPI schema

The course create, replace, and partial-update request examples include the
`version` field. Search Swagger for `version` or open these operations:

- `POST /api/v1/courses/`
- `PUT /api/v1/courses/{id}/`
- `PATCH /api/v1/courses/{id}/`
- `GET /api/v1/course-versions/`

## Admin Responsibility

Creators do not create or edit version records. Admins manage the canonical
version catalogue through:

```http
GET  /api/v1/admin/course-versions/
POST /api/v1/admin/course-versions/
GET  /api/v1/admin/course-versions/{id}/
PATCH /api/v1/admin/course-versions/{id}/
POST /api/v1/admin/course-versions/migrations/
```

Only active records appear in the creator picker. Freezing a version prevents
new selections but does not rewrite existing course assignments.

## Acceptance Checklist

- [ ] The Versioning step loads its options from `GET /course-versions/`.
- [ ] The picker stores a UUID, not a label.
- [ ] Selecting a version sends `PATCH /courses/{id}/` with `{ "version": "<uuid>" }`.
- [ ] Returning to the step displays the saved label from course detail.
- [ ] Submission is blocked or redirected to Versioning when no version exists.
- [ ] The submission error is mapped to the version control.
- [ ] No version is selected automatically when the active list is empty.
- [ ] Swagger shows `version` in POST, PUT, and PATCH request examples.

