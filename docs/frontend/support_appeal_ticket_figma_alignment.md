# Support page — "Request for an appeal" & "Submit a ticket" (Figma alignment)

This is the frontend contract for the two Support-page forms in the Help →
Support Figma frames, plus their success modal ("Toast_Succesful" /
"Your Ticket Has Been Submitted"). Everything below is **already
implemented and tested** in the backend
(`api/support/`, tests in `api/support/tests/test_support_api.py`) — the
frontend only needs to follow this mapping; no backend change is required.

An extended handover covering the whole Support page (including the public
"Contact us" form and the admin queue) lives in
[FRONTEND_HANDOVER.md](FRONTEND_HANDOVER.md#support-help--support).

---

## Screen 1 — Request for an appeal

**Figma frame:** Support page action "Request appeal" → "Request for an
appeal" form → success toast.

**Endpoint:** `POST /api/v1/support/appeals/`

**Auth:** Any signed-in user. A suspended creator can still sign in and
appeal — never block this route on account status.

### Figma field → request body

| Figma field | Body field | Required | Rules |
|---|---|---|---|
| Title | `title` | ✅ | Max 255 chars |
| Email | `email` | ✅ | Valid email address |
| Web link (optional url input) | `web_link` | ❌ | Full URL (`https://…`); omit or send empty string |
| Description (multiline) | `description` | ✅ | Free text |

That is the whole body — four fields (the ticket form has a different
body; see Screen 2). If a Figma variant shows extra
inputs (e.g. a course picker or dropdown): the Support-page appeal is
account-level and does **not** take a course.

Disputing one rejected course is a different feature, `POST
/api/v1/course-appeals/` (body needs `course`), reached from the course
list — see [content_review_chain_handover.md](content_review_chain_handover.md).

### 201 Response

```json
{
  "id": "0b7c3f5e-6a1d-4e2b-9c8f-2d4a6b8c0e13",
  "kind": "APPEAL",
  "title": "My account was suspended in error",
  "email": "creator@example.com",
  "web_link": "https://example.com/portfolio",
  "message": "The flagged lesson was original work.",
  "status": "OPEN",
  "due_at": "2026-10-08T11:00:00Z",
  "created_datetime": "2026-09-29T11:00:00Z"
}
```

`message` echoes the form's Description. `due_at` is appeals-only — the
PRD dispute-resolution SLA of **7 business days**; display it on the
success screen ("A response is due by …") rather than the raw timestamp.

### Success modal (Figma Toast_Succesful)

Show on 201. Two details worth wiring to the response, not hardcoding:

- The submitted item summary uses the response `title`.
- Appeals get `due_at`; tickets do not.

Also noting: the API notified Support staff in-app server-side — the
frontend must **not** send its own notification.

### Errors (400 validation)

Error shape:

```json
{ "errors": [ { "type": "validation_error",
                "code": "invalid",
                "message": "Enter a valid URL.",
                "field_name": "web_link" } ] }
```

Branch on `field_name` to mark the failing input; `web_link` 400s if the
value is not a full URL (e.g. `example.com`, bare `https://`). Missing
required fields report their `field_name` with code `required`.

Unauthenticated submissions 401 — route to sign-in and return the user to
the form afterwards.

---

## Screen 2 — Submit a ticket

**Figma frame:** Support page action "Create ticket" → "Submit a ticket"
form → "Ticket created" modal ("Kindly check your email to track the
progress of this ticket" → **Go home**).

**Endpoint:** `POST /api/v1/support/tickets/`

| Figma field | Body field | Required | Rules |
|---|---|---|---|
| Subject | `subject` | ✅ | Max 255 chars |
| Email | `email` | ✅ | Valid email; confirmation + updates are emailed here |
| Priority (dropdown) | `priority` | ✅ | `LOW`, `MEDIUM` or `HIGH` |
| Description | `description` | ✅ | Free text |

This body is **different from the appeal**: there is no `title` and no
`web_link` (both are ignored if sent). The response uses the shared
support-request shape, so `subject` comes back as `title`, with
`kind: "TICKET"`, `priority` set and `due_at: null`. A confirmation email
goes to `email`.

## Screen 3 — Help → Tickets tab (creator)

| Action | Endpoint |
|---|---|
| My tickets (filter `?status=`, `?priority=`) | `GET /api/v1/support/tickets/` |
| Ticket + thread | `GET /api/v1/support/tickets/{id}/` |
| Reply | `POST /api/v1/support/tickets/{id}/messages/` body `{"body": "..."}` |

The detail adds `submitted_by` (`id`, `name`, `email`, `role`) and
`messages` (oldest first). Render the ticket's own `message` as the first
bubble, then each entry:

- `kind: "REPLY"`: a bubble; `from_support: true` → support side.
- `kind: "STATUS_CHANGE"`: the "Ticket status changed to {to_status}" line.

Replying to a `CLOSED` ticket returns 400 (`field_name: "body"`).

## Admin Tickets page

All under `/api/v1/support/admin/tickets/`. Each needs `support.manage_requests`
(Admin / Super Admin).

| Figma element | Endpoint |
|---|---|
| Cards (Opened / Resolved / Closed / In-Progress) | `GET stats/` → `{open, in_progress, resolved, closed}` |
| Table | `GET ` (paginated) — `search`, `status`, `priority`, `date_from`, `date_to`, `ordering` |
| Ticket Overview drawer | `GET {id}/` (same shape as the creator detail, plus `assigned_to_name` = Support officer) |
| Add a response | `POST {id}/messages/` `{"body": "..."}` |
| Close / Mark in-progress / Mark resolved | `POST {id}/status/` `{"status": "CLOSED" \| "IN_PROGRESS" \| "RESOLVED"}` |
| Bulk bar (checkbox selection) | `POST bulk-status/` `{"ids": [...], "status": ...}` → `{updated: [...], skipped: [{id, reason}]}` |

Table columns map to `id` (Ticket ID), `submitted_by_name` (Created by),
`title` (Subject), `created_datetime`, `status`, `priority`. "Last updated"
is `updated_datetime`.

Status values are `OPEN`, `IN_PROGRESS`, `RESOLVED`, `CLOSED`. Allowed moves:
Open → any; In-progress → Resolved/Closed; Resolved → In-progress/Closed;
Closed is final. Each move adds a thread entry, sets the support officer
(first admin to act) and notifies the creator in-app and by email. Show the
"Ticket successfully marked as closed" toast on the 200.

---

## Verification performed

- `api/support/tests/test_support_api.py` and `test_ticket_api.py` all pass
  against the Postgres test database.
- Swagger tags: `Support — Appeals`, `Support — Tickets`, `Admin — Support`.
