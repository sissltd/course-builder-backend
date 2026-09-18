# Course Collaborator Frontend Handover

This document describes the frontend integration for course collaboration:
inviting creators, showing pending invites, accepting or declining invites,
managing collaborators, assigning modules, and handling access restrictions.

The API base used in examples is:

```text
/api/v1
```

All requests below require the normal authenticated access token unless stated
otherwise.

## Collaboration model

There are two collaborator roles:

| Role | Access |
|---|---|
| `COLLABORATOR` | Access only to explicitly assigned modules. |
| `ADMIN` | Full course access. Module restrictions do not apply. |

The course creator is the Author and is not stored as a collaborator row.

An invite is initially `PENDING`. It becomes `ACCEPTED`, `DECLINED`, or
`REVOKED`. Pending invites expire after 14 days. Creating a new invite for the
same course and email revokes the previous pending invite.

Creating an invite does not grant access. Access is granted only after the
invitee accepts it.

## API routes

### Send an invite

```http
POST /api/v1/course-invites/
```

Creator or Admin collaborator only.

Request:

```json
{
  "course_id": "course-uuid",
  "email": "jane@example.com",
  "role": "COLLABORATOR",
  "assigned_modules": ["module-uuid-1", "module-uuid-2"]
}
```

`role` defaults to `COLLABORATOR`. `assigned_modules` is optional for an
`ADMIN` invite and is ignored for Admin access. For a plain collaborator,
send the module IDs they should be able to edit.

Successful response: `201 Created`.

```json
{
  "id": "invite-uuid",
  "course": "course-uuid",
  "email": "jane@example.com",
  "invitee": {
    "id": "user-uuid",
    "name": "Jane Doe",
    "email": "jane@example.com"
  },
  "role": "COLLABORATOR",
  "assigned_modules": [
    {
      "id": "module-uuid-1",
      "title": "Getting Started",
      "order": 1
    }
  ],
  "status": "PENDING",
  "is_expired": false,
  "expires_at": "2026-10-02T14:09:55.885489Z",
  "responded_at": null,
  "created_datetime": "2026-09-18T14:09:55.886289Z"
}
```

The response does not contain a secret invite token. Use the invite `id` when
handling the invitation flow.

### List invites for a course

```http
GET /api/v1/course-invites/?course_id={course_id}
```

Creator or Admin collaborator only. This returns all invite history for the
course, newest first.

The response uses the standard paginated envelope:

```json
{
  "status": true,
  "message": "Successfully retrieved data",
  "data": {
    "paginator": {
      "count": 1,
      "page": 1,
      "page_size": 10,
      "total_pages": 1,
      "next": null,
      "next_page_number": null,
      "previous": null,
      "previous_page_number": null
    },
    "results": []
  }
}
```

`course_id` is required. An omitted value returns `400`.

### List the signed-in user's incoming invites

```http
GET /api/v1/course-invites/incoming/
```

This returns only `PENDING` invites whose email matches the authenticated
user's email address. It is the source of truth for the invitee inbox and for
resolving an email deep link after login or signup.

Do not rely on `GET /course-invites/{invite_id}/` to load an invite for a user
who has not accepted it yet. The invitee should load `/incoming/`, find the
matching `id`, and then accept or decline it.

### Accept an invite

```http
POST /api/v1/course-invites/{invite_id}/accept/
```

The authenticated user's email must match the invite email.

Successful response:

```json
{
  "detail": "Invite accepted.",
  "collaborator_id": "collaborator-uuid",
  "course_id": "course-uuid"
}
```

After success:

1. Invalidate incoming invites.
2. Invalidate the course/collaborator cache.
3. Redirect to the course editor or the accepted course page.
4. Load the course again so module visibility reflects the new assignment.

### Decline an invite

```http
POST /api/v1/course-invites/{invite_id}/decline/
```

Successful response:

```json
{
  "detail": "Invite declined.",
  "invite_id": "invite-uuid"
}
```

### Revoke a pending invite

```http
DELETE /api/v1/course-invites/{invite_id}/
```

Creator or Admin collaborator only. The response is `204 No Content`.
Accepted and declined invites cannot be revoked.

### List collaborators

```http
GET /api/v1/collaborators/
GET /api/v1/collaborators/?course_id={course_id}
```

The unscoped route lists assignments across courses owned by the caller. Use
`course_id` for a course's management screen. An existing collaborator can
view collaborators on a course they can access.

Supported filters:

```text
course_id={uuid}
category={uuid}
search={name-or-email}
role=COLLABORATOR|ADMIN
date_from=YYYY-MM-DD
date_to=YYYY-MM-DD
```

Successful rows contain:

```json
{
  "id": "collaborator-uuid",
  "name": "Jane Doe",
  "email": "jane@example.com",
  "country_of_origin": "NG",
  "date_added": "2026-07-20T11:00:00.000Z",
  "role": "COLLABORATOR",
  "role_label": "Collaborator",
  "course_id": "course-uuid",
  "course_title": "Intro to Python",
  "category": {
    "id": "category-uuid",
    "name": "Software Engineering"
  },
  "assigned_modules": [
    {
      "id": "module-uuid",
      "title": "Getting Started",
      "order": 1
    }
  ]
}
```

### Update role or module assignment

```http
PATCH /api/v1/collaborators/{collaborator_id}/
```

Creator or Admin collaborator only. Send only the fields being changed.

Promote to Admin:

```json
{
  "role": "ADMIN"
}
```

Assign modules to a plain collaborator:

```json
{
  "assigned_modules": ["module-uuid-1", "module-uuid-2"]
}
```

`assigned_modules` replaces the complete assignment list; it does not append
to the existing list. An Admin has full-course access regardless of the
stored module list.

### Remove a collaborator

```http
DELETE /api/v1/collaborators/{collaborator_id}/
```

Creator or Admin collaborator only. The response is `204 No Content` and
access is revoked immediately. There is no undo operation.

## Email and deep-link flow

When an invite is created, the backend sends a link in this shape:

```text
{FRONTEND_URL}/auth/login?callbackUrl=%2Fcreator%2Finvitations%3Finvite_id%3D{invite_id}
```

Example after URL decoding:

```text
/auth/login?callbackUrl=/creator/invitations?invite_id=invite-uuid
```

### Required frontend behavior

1. Read and retain the `callbackUrl` query parameter on the login page.
2. After successful login, redirect to the callback URL instead of the
   default dashboard.
3. If the user does not have an account, preserve the same callback through
   signup and email verification.
4. On `/creator/invitations?invite_id={id}`, call
   `GET /api/v1/course-invites/incoming/`.
5. Find the invite by `id`; do not trust an invite ID without checking that it
   is present in the authenticated user's incoming results.
6. Show the course, role, assigned modules, expiry, and Accept/Decline actions.
7. After accepting, redirect to the course and refresh permissions/data.

If the callback is missing, show the regular incoming-invites page rather than
redirecting the user to the dashboard and hiding the pending invite.

The callback is an internal path. The frontend must reject external callback
URLs to avoid an open redirect. Only paths beginning with `/creator/` should
be accepted.

## Suggested frontend state

```ts
type CollaboratorRole = "COLLABORATOR" | "ADMIN";
type InviteStatus = "PENDING" | "ACCEPTED" | "DECLINED" | "REVOKED";

type CourseInvite = {
  id: string;
  course: string;
  email: string;
  invitee: {
    id: string;
    name: string;
    email: string;
  } | null;
  role: CollaboratorRole;
  assigned_modules: Array<{
    id: string;
    title: string;
    order: number;
  }>;
  status: InviteStatus;
  is_expired: boolean;
  expires_at: string;
  responded_at: string | null;
  created_datetime: string;
};
```

Recommended cache keys:

```text
incomingInvites
courseInvites(courseId)
courseCollaborators(courseId)
course(courseId)
modules(courseId)
```

Invalidate the relevant keys after sending, revoking, accepting, declining,
assigning modules, changing roles, or removing collaborators.

## Access and module behavior

After acceptance, a plain collaborator can access only assigned modules.
Unassigned modules should be hidden or read-only according to the course
editor's existing UX. Do not assume that a successful course-level response
means every module is writable.

The backend remains authoritative. A collaborator may receive `403` or `404`
when attempting an operation outside their course/module scope.

The creator can also persistently freeze collaborator editing using:

```http
POST /api/v1/courses/{course_id}/modules/{module_id}/collaboration-lock/
POST /api/v1/courses/{course_id}/modules/{module_id}/collaboration-unlock/
```

See [MODULE_COLLABORATION_LOCK_FRONTEND_GUIDE.md](./MODULE_COLLABORATION_LOCK_FRONTEND_GUIDE.md)
for the lock-specific UI and `423 Locked` behavior.

## Error handling

The API uses the standard error envelope:

```json
{
  "errors": [
    {
      "type": "validation_error",
      "code": "invalid",
      "message": "This invite has expired. Ask the course owner to send a new one.",
      "field_name": null
    }
  ]
}
```

Handle these cases explicitly:

| Status | Frontend behavior |
|---|---|
| `201` | Add the pending invite to the course invite list and show success. |
| `204` | Remove the revoked/removed row from the current list. |
| `400` | Show field validation or an invite state message. Do not retry automatically. |
| `401` | Refresh the access token or send the user to login. |
| `403` | Show that the user lacks course management permission. |
| `404` | Treat the invite/course as unavailable; do not expose whether another user's invite exists. |
| `423` | Show the module is locked for collaborator editing and refresh module state. |
| `429` | Respect the API retry-after behavior and avoid duplicate invite submissions. |
| `500` | Show a retryable server error and preserve the form values. |

Common validation messages include:

- The course creator cannot be invited as a collaborator.
- The user is already a collaborator on this course.
- The invite has expired or is no longer open.
- The caller is not the invited email address.
- Assigned modules must belong to the selected course.

## Swagger and local testing

Use the API Swagger page to verify the deployed base URL and authorization:

```text
{API_BASE_URL}/api/schema/swagger-ui/
```

Local API:

```text
http://localhost:8000/api/schema/swagger-ui/
```

Example invitee flow:

```bash
curl -X GET \
  'http://localhost:8000/api/v1/course-invites/incoming/' \
  -H 'Authorization: Bearer <invitee-access-token>'

curl -X POST \
  'http://localhost:8000/api/v1/course-invites/<invite-id>/accept/' \
  -H 'Authorization: Bearer <invitee-access-token>'
```

Example creator flow:

```bash
curl -X POST \
  'http://localhost:8000/api/v1/course-invites/' \
  -H 'Authorization: Bearer <creator-access-token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "course_id": "<course-id>",
    "email": "jane@example.com",
    "role": "COLLABORATOR",
    "assigned_modules": ["<module-id>"]
  }'
```

The backend lifecycle tests are in:

- `api/collaborators/tests/test_invite_api.py`
- `api/collaborators/tests/test_collaborator_api.py`

These tests are useful references for expected status codes, response fields,
permission boundaries, and invite transitions.

## Frontend completion checklist

- [ ] Preserve `callbackUrl` from invitation email through login and signup.
- [ ] Validate callback URLs as internal paths.
- [ ] Add an incoming invitations page or drawer.
- [ ] Load incoming invites from `/course-invites/incoming/`.
- [ ] Match the deep-linked `invite_id` against incoming results.
- [ ] Implement Accept and Decline actions with loading and error states.
- [ ] Redirect to the course after acceptance.
- [ ] Add creator course invite management.
- [ ] Add resend/re-invite behavior using the create endpoint.
- [ ] Add revoke behavior for pending invites.
- [ ] Add collaborator role and module assignment controls.
- [ ] Refresh course/module permissions after every access change.
- [ ] Handle `403`, `404`, `423`, `429`, and `500` without losing form state.
- [ ] Add tests for login deep links, invite acceptance, decline, expiry, and
  mismatched-email behavior.
