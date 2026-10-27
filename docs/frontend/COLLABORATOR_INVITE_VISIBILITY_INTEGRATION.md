
# Collaboration invites: making the invitee actually see them

Companion to `COLLABORATOR_FRONTEND_HANDOVER.md`, which documents the full
collaborator surface. This one covers a single failure: **an invited person
signs in and never sees the invite.** It lists the backend contract that is now
in place and the frontend work still required.

## The bug this addresses

An invite was sent to a real account. The invite existed, the invitee received
the in-app notifica
tion — and still saw nothing actionable. Two causes:

1. The emailed link aimed at `/auth/login?callbackUrl=...`. A recipient who
   already had a session hit the route guard's "already authenticated, leave
   /auth" redirect, which sent them to the dashboard and discarded the
   callback — so the invite was never reached.
2. The invitee's Invitations page queried the **sender's** endpoint
   (`GET /course-invites/?course_id=…`), which `404`s for someone who does not
   own the course. The page rendered empty.

The lesson in the fix: **the email link must not be the only way to reach an
invite.** It has to survive login, and for a new user signup and email
verification too. Treat it as an accelerator over a path that already works.

## Build it in three layers

Ship in this order. Layer 1 alone unblocks the reported bug.

### Layer 1 — the Invitations page reads `/incoming/` (required)

`/creator/invitations` serves two different people. Split the data source by
who is looking, not by route:

| Tab | Endpoint | Shown to |
|---|---|---|
| Received | `GET /api/v1/course-invites/incoming/` | everyone |
| Sent | `GET /api/v1/course-invites/?course_id={id}` | course creators / Admin collaborators |

Once Received is wired up, the invitee clicks **Invitations** in the sidebar and
the invite is there — no email, no deep link, no dependence on the redirect.

### Layer 2 — make the bell notification a deep link (strongly recommended)

The invite notification already carries what you need. From
`GET /api/v1/users/me/notifications/`:

```json
{
  "id": "…",
  "title": "Course collaboration invite",
  "content": "Banjo Joshua invited you to collaborate on 'Technical Report Writing'.",
  "metadata": { "course_id": "…", "invite_id": "2f56263d-…" }
}
```

Route a click on any notification with `metadata.invite_id` to
`/creator/invitations?invite_id={metadata.invite_id}`. The user is already
authenticated, so this path has none of the email link's fragility. For anyone
who already has an account it is the better entry point.

### Layer 3 — route by session state, not by always landing on login

The email now links straight at the invite:

```text
https://www.soludeskscb.com/creator/invitations?invite_id={invite_id}
```

The route guard splits the two cases on its own:

| Recipient | What happens |
|---|---|
| Already signed in | Lands on the invite directly — no login hop |
| No session / no account | Guard redirects to login or signup, then back here |

Aiming the email at `/auth/login?callbackUrl=…` breaks the first row. A
signed-in caller hits the "already authenticated, leave /auth" redirect, which
sends them to the dashboard and **drops the callback** — the invite is never
shown. That was the original bug.

**No frontend change is needed for this.** It was fixed entirely in the
backend by changing the link the email carries. Verified in a browser against
the current frontend, unmodified:

| Scenario | Lands on | Invite shown |
|---|---|---|
| Already signed in | `/creator/invitations?invite_id=…` | yes |
| No session | login hop → `/creator/invitations?invite_id=…` | yes |

What must stay true on the frontend — all of it already does:

1. `/creator/*` stays behind the route guard, so a session-less visit
   redirects to login with `callbackUrl` set to the requested path.
2. The callback survives signup **and email verification** — an invite can be
   sent to an address with no account, which is the only path those users have.
3. Only same-site paths are accepted as a callback — anything else is an open
   redirect — and a callback never points back into `/auth/*`, or the guard
   loops.
4. If `invite_id` is missing or rejected, land on the Invitations page, never
   the dashboard.

#### Optional: invites mailed before this change

Invites already in people's inboxes still carry the old
`/auth/login?callbackUrl=…` shape and keep it for their remaining 14 days. For
a recipient who already has a session those links still land on the dashboard,
because the guard's "already authenticated, leave `/auth`" branch drops the
callback.

If you want to rescue them, honour a safe internal `callbackUrl` in that branch
instead of sending the caller to the dashboard — reusing the existing
`sanitizeInternalPath`, and refusing any target under `/auth/*` so the guard
cannot loop. Purely optional: every invite sent from now on bypasses `/auth`
entirely, so this only shortens the tail.

## Endpoints

All paths are under `/api/v1/`. Every call needs
`Authorization: Bearer <access-token>`.

### Read the invitee's inbox

```http
GET /api/v1/course-invites/incoming/
```

Open invites addressed to the session's email — `PENDING` and not expired.
**No course permission required**, only a signed-in session. Every row returned
is safe to render with an Accept button; expired invites are filtered out
server-side.

### Resolve a deep-linked `invite_id`

```http
GET /api/v1/course-invites/{invite_id}/
```

Readable by the invitee (matched on session email) or by someone with access to
the course. To anyone else it returns `404` — the API never confirms that
another person's invite exists.

Unlike `/incoming/`, this returns invites in **any** state. That is deliberate:
it lets the page explain *why* there is no action instead of rendering blank.
Branch on the payload:

| `status` | `is_expired` | Render |
|---|---|---|
| `PENDING` | `false` | Accept / Decline |
| `PENDING` | `true` | "This invite expired. Ask the course owner to send a new one." |
| `ACCEPTED` | — | "You already accepted this." Link to the course. |
| `DECLINED` | — | "You declined this invite." |
| `REVOKED` | — | "The course owner cancelled this invite." |

Do not treat a `404` here as an error state to retry — it means "not yours, or
no such invite". Show the inbox instead.

### Accept / decline

```http
POST /api/v1/course-invites/{invite_id}/accept/
POST /api/v1/course-invites/{invite_id}/decline/
```

No request body. No course permission required — the session email must match
the invite, which the API enforces. Accept returns:

```json
{ "detail": "Invite accepted.", "collaborator_id": "…", "course_id": "…" }
```

After accepting, refresh cached permissions and course lists — the user's
access just changed — then route to `course_id`.

## Payload shape

`GET /course-invites/{id}/` returns the invite flat:

```json
{
  "id": "a472965e-…",
  "course": "999ca98b-…",
  "course_title": "Technical Report Writing",
  "email": "ogbonnayakingsike@gmail.com",
  "invitee": { "id": "…", "name": "King Doe", "email": "ogbonnayakingsike@gmail.com" },
  "invited_by": { "id": "…", "name": "Banjo Joshua", "email": "…" },
  "role": "COLLABORATOR",
  "role_label": "Collaborator",
  "assigned_modules": [],
  "status": "PENDING",
  "is_expired": false,
  "expires_at": "2026-11-06T10:23:36Z",
  "responded_at": null,
  "created_datetime": "2026-10-23T10:23:36Z"
}
```

`course_title`, `role_label` and `invited_by` are embedded on purpose: an
invitee has no access to the course until they accept, so `GET /courses/{id}/`
would `404` for the very person this payload is for. Render
"{invited_by.name} invited you to collaborate on {course_title} as
{role_label}" entirely from this response — do not fetch the course first.

`invitee` is `null` when the address has no account yet. `invited_by` is `null`
only if that account was since deleted.

### Envelope gotcha

List and detail responses are shaped differently:

```js
// Paginated (/incoming/, /?course_id=) — wrapped
const invites = res.data.data.results
const total   = res.data.data.paginator.count   // drives the sidebar badge

// Detail (/{id}/, accept, decline) — flat, no envelope
const invite  = res.data
```

## Errors

| Status | Meaning | Do |
|---|---|---|
| `400` | Invite not open — already answered, revoked, or expired | Show `errors[0].message`. Never auto-retry. |
| `401` | Session expired | Re-auth, preserving the current URL as the callback. |
| `403` | No manage access (sender-side actions only) | Hide the control rather than showing a failing button. |
| `404` | Not yours, or does not exist | Fall back to the inbox. Do not reveal that it might exist. |

Error bodies:

```json
{ "errors": [ { "type": "validation_error", "code": "invalid",
                "message": "This invite has expired. Ask the course owner to send a new one.",
                "field_name": null } ] }
```

Invites expire 14 days after creation, and re-inviting the same email on the
same course revokes the previous pending invite — a user holding two links for
one course will find the older one `REVOKED`.

## Checklist

- [ ] Received tab reads `/course-invites/incoming/`; Sent tab keeps `?course_id=`
- [ ] Sidebar badge from `data.paginator.count` on `/incoming/`
- [ ] Pending-invite banner on the dashboard — the safety net when routing fails
- [ ] Bell notifications with `metadata.invite_id` deep-link internally
- [ ] `callbackUrl` preserved through login, signup, and email verification
- [ ] Callback validated as an internal `/creator/` path
- [ ] Signed-in recipient reaches the invite without a login hop
- [ ] *(optional)* Legacy `/auth/login?callbackUrl=…` invites rescued
- [ ] Deep link resolved with `GET /course-invites/{id}/`, all five states handled
- [ ] Accept/Decline wired with loading and error states
- [ ] Permissions and course lists refreshed after accepting
- [ ] Verified end-to-end with an account that holds no `courses.create`

## Backend changes behind this document

Shipped alongside it, in `api/collaborators/`:

1. `incoming`, `retrieve`, `accept` and `decline` no longer require the
   `courses.create` permission — only a signed-in session. An invite is
   addressed to an email, not a role; previously a recipient whose role lacked
   that permission was `403`'d out of the invite that would have granted them
   access. Rows stay scoped to the caller's own email.
2. `GET /course-invites/{id}/` now resolves for the invitee. Before, the only
   endpoint keyed on `invite_id` — the thing the email link carries — `404`'d
   for the recipient.
3. `/incoming/` excludes expired invites, which previously appeared actionable
   and then failed with a `400` on accept.
4. The invite payload gained `course_title`, `role_label` and `invited_by`.
5. The invitation email links to `/creator/invitations?invite_id={id}` instead
   of `/auth/login?callbackUrl=…`, so a recipient who already has a session
   reaches the invite instead of being redirected to their dashboard.

Every fix above is backend-only. No frontend change was required for any of
them.
