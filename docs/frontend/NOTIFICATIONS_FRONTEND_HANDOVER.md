# Notifications Page & Notification Settings — Frontend Handover

This covers the **Notifications** page (All / Unread tabs, Mark as read, Mark
all as read, the bell badge), the **live notification stream**, and the
**Settings → Notifications** screen. For each design element it gives the
endpoint, the exact request and response, and what to hide.

All paths are under `/api/v1/`, and every endpoint here needs the normal
`Authorization: Bearer <access token>` header. The Swagger tag is
`Users — Notifications`.

The other Settings tabs (Account, Permissions, Platform, Payments, Achievement
badge, Security) are in
[settings_and_achievements_handover.md](settings_and_achievements_handover.md).

---

## Read first — what changed

1. **The live stream now pushes.** Before this change, the stream sent the
   first event on connect and then never sent anything, whatever happened.
   It now pushes whenever the user gets a notification, marks one read or
   unread, or marks all read. If your client already handles stream events,
   expect them to start arriving.
2. **Every stream event is a JSON array**: the user's newest 20 in-app
   notifications, newest first. That includes the first event and every push
   after it. Replace your list with each event; don't append it. (The old
   Swagger text said "a notification object". That was wrong and is fixed.)
3. **Stream timestamps now match the list endpoint**: ISO 8601 in UTC, e.g.
   `"2026-10-04T22:52:53.526775Z"`. They used to be
   `"2026-10-04 22:52:53.526775+00:00"`, which Safari's `new Date()` can't
   parse. If you wrote a workaround for the old format, remove it.
4. **New: `GET users/me/notifications/unread-count/`** for the badge and the
   "Unread (n)" tab.
5. **New: `POST users/me/notifications/mark-all-read/`** for "Mark all as
   read".
6. **New preference: `course_update`** (default `true`), behind the "Course
   Update" toggle. Admins now get an in-app notification when someone else
   submits, approves, rejects or publishes a course (details in Part 5).
7. **`creator_feedback` now works.** Admins who decide appeals and switch it
   off no longer get "New course-rejection appeal" notifications. It used to
   be saved and ignored.

8. **Settings → Payments: `GET platform-settings/` now returns
   `auto_credit_duration_hours` and `withdrawal_require_verification`.**
   `PATCH` already accepted them, but `GET` left them out, so the Payments tab
   couldn't show saved values. Details are in
   [settings_and_achievements_handover.md](settings_and_achievements_handover.md).

Nothing was removed or renamed.

---

## Part 1 — Endpoints at a glance

| Design element | Call |
|---|---|
| All tab | `GET users/me/notifications/` |
| Unread tab | `GET users/me/notifications/?is_read=false` |
| "Unread (12)" label, bell badge | `GET users/me/notifications/unread-count/` |
| Mark as read (one row) | `POST users/me/notifications/toggle-read/` |
| Mark all as read | `POST users/me/notifications/mark-all-read/` |
| Live updates while the app is open | `GET users/me/notifications/streamed-notifications/` (SSE) |
| Settings → Notifications | `GET` / `PATCH users/me/notification-preferences/` |

Every endpoint here returns only the **caller's own** notifications. None of
them takes a user id.

---

## Part 2 — Notifications page

### The notification object

The list endpoint and the stream both return items in this shape:

```json
{
  "id": "352fad93-a5df-4301-877d-ef90af1b01d9",
  "title": "Course submitted",
  "content": "'Intro to Data Analysis' was submitted for review.",
  "content_type": "TEXT",
  "is_read": false,
  "created_datetime": "2026-10-04T22:52:53.526775Z",
  "metadata": {
    "course_id": "4c3ccb6a-7556-46a1-98e1-e0e3b85f3bcb",
    "status": "SUBMITTED"
  }
}
```

| Field | Notes |
|---|---|
| `id` | UUID. Send it to `toggle-read`. |
| `title` | The bold first line in the design. |
| `content` | The grey body line. |
| `content_type` | Always `TEXT` today. Render `content` as plain text. |
| `is_read` | Drives the blue unread dot and the "Mark as read" button. |
| `created_datetime` | ISO 8601, UTC. Format it in the user's timezone (`timezone` on `GET users/me/`). |
| `metadata` | Free-form and different for each kind of notification. Most carry `course_id`. Treat every key as optional. |

**There is no `type` or `category` field.** The design shows different icons
(a tick and a document), but the backend doesn't classify notifications. Use
one icon for all of them, or choose one from a `metadata` key you check for,
such as `course_id`. If product needs a real category, raise it with backend.

### Today / Yesterday grouping

Do this on the client from `created_datetime`, in the user's timezone. The API
doesn't group.

### List: All and Unread tabs

```http
GET /api/v1/users/me/notifications/
GET /api/v1/users/me/notifications/?is_read=false
```

| Param | Values | Notes |
|---|---|---|
| `is_read` | `true` / `false` | Omit it for All. `false` = the Unread tab. |
| `size` | integer | Page size. Default 10. |
| `cursor` | opaque string | Don't build it yourself. Follow `data.paginator.next`. |

Newest first. Pagination is **cursor-based**: there are no page numbers and no
total count. Use `data.paginator.next` (a full URL, or `null` at the end) for
"load more" or infinite scroll.

```json
{
  "status": true,
  "message": "Successfully retrieved data",
  "data": {
    "paginator": {
      "next": "https://api.example.com/api/v1/users/me/notifications/?cursor=cD0yMDI2LTEw...",
      "previous": null
    },
    "results": [ { "...": "notification object, as above" } ]
  }
}
```

> Note: `status` here is the boolean `true`, while the other endpoints in
> this doc return `"status": 200` plus `"success": true`. That's existing
> behaviour of the shared cursor paginator, not something new.

**Empty state** ("No notifications / Please come back later"): show it when
`data.results` is `[]` and `data.paginator.previous` is `null`.

### Unread count

```http
GET /api/v1/users/me/notifications/unread-count/
```

```json
{
  "status": 200,
  "success": true,
  "message": "Unread notification count retrieved.",
  "data": { "unread_count": 12 }
}
```

Use it for "Unread (12)" and the bell badge. It counts only the caller's
unread in-app notifications. Refetch it after each stream event (see Part 3);
the stream doesn't carry the count.

### Mark one as read (or unread)

```http
POST /api/v1/users/me/notifications/toggle-read/
Content-Type: application/json

{ "notification_id": "352fad93-a5df-4301-877d-ef90af1b01d9", "read_status": true }
```

```json
{
  "status": 200,
  "success": true,
  "message": "Notification 352fad93-a5df-4301-877d-ef90af1b01d9 marked as read."
}
```

- `read_status: false` marks it unread again. Both directions are idempotent.
- No `data` comes back. Update the row locally, or wait for the stream push.
- A notification that doesn't exist or isn't the caller's returns **404**.

### Mark all as read

```http
POST /api/v1/users/me/notifications/mark-all-read/
```

No request body.

```json
{
  "status": 200,
  "success": true,
  "message": "All notifications marked as read.",
  "data": { "updated": 12 }
}
```

- `updated` is how many changed. Calling it again returns `"updated": 0`, not
  an error, so you don't need to disable the button.
- Only the caller's notifications change.
- After it succeeds, set the badge to 0 and clear the unread dots. The stream
  also pushes the refreshed list.

---

## Part 3 — Live stream (SSE)

```http
GET /api/v1/users/me/notifications/streamed-notifications/
Authorization: Bearer <access token>
Accept: text/event-stream
```

Open it once per session, e.g. when the dashboard shell mounts. It stays open
until the client closes it.

### You can't use the browser's `EventSource`

Native `EventSource` can't send an `Authorization` header, and the backend
doesn't accept the token as a query parameter, so `EventSource` will always
get a **401**. Use a fetch-based SSE client that can set headers, such as
`@microsoft/fetch-event-source`, or read the `fetch()` response body stream
yourself.

```ts
import { fetchEventSource } from "@microsoft/fetch-event-source";

await fetchEventSource(`${API}/api/v1/users/me/notifications/streamed-notifications/`, {
  headers: { Authorization: `Bearer ${getAccessToken()}` },
  signal: abortController.signal,
  onmessage(ev) {
    const latest = JSON.parse(ev.data); // array, newest first, max 20
    setLatestNotifications(latest);      // replace the list, don't append
    refetchUnreadCount();                // the stream carries no count
  },
  onerror(err) {
    // returning lets the library retry; throw to stop
  },
});
```

### What each event contains

Every event is a single `data:` line holding a JSON **array** of the user's
newest 20 in-app notifications, newest first, in the object shape from Part 2.
Events have no `event:` name and no `id:`.

```text
data: [{"id": "352fad93-…", "title": "Course submitted", "content": "…", "content_type": "TEXT", "is_read": false, "created_datetime": "2026-10-04T22:52:53.526775Z", "metadata": {"course_id": "4c3c…", "status": "SUBMITTED"}}]
```

| When | What arrives |
|---|---|
| On connect | The current newest 20 (`[]` if none) |
| The user gets a new notification | The refreshed newest 20, the new one first |
| The user marks one read or unread (any tab or device) | The refreshed newest 20 |
| The user marks all read | The refreshed newest 20, all `is_read: true` |

Because each event is the complete current list, a client that missed events
(tab asleep, reconnect) is back in sync after the next one. You don't need to
replay anything.

### Reconnecting

- The server never closes the stream on purpose, and it sends **no
  heartbeat**. A proxy or a sleeping laptop may still drop an idle
  connection, so reconnect with exponential back-off (e.g. 1s, 2s, 4s… up to
  30s).
- The token is checked **once, at connect**. Access tokens last 30 minutes,
  so refresh the token before each reconnect. A reconnect with an expired
  token gets **401**: refresh and retry, don't loop on the 401.
- After a reconnect, the first event re-syncs the list. Refetch the unread
  count as well.

### What the stream does not cover

- **Older notifications.** It carries the newest 20 only. The Notifications
  page should use the list endpoint for history and paging.
- **The unread count.** Call `unread-count/` after each event.
- **Users with in-app notifications switched off** (`in_app_enabled: false`).
  No notification is created for them, so nothing is pushed, except critical
  notices that are always delivered: account suspended or reinstated, wallet
  adjusted, role changed, repeated-login lockout, and the MIE circuit breaker.

---

## Part 4 — Settings → Notifications

```http
GET   /api/v1/users/me/notification-preferences/
PATCH /api/v1/users/me/notification-preferences/
```

The first `GET` creates the row with every toggle on. `PATCH` takes any subset
of fields. For "Save changes", send only the fields that changed. An empty body
returns **400**.

```json
{
  "id": "0b7c…",
  "new_course_assigned": true,
  "escalation_assigned": true,
  "creator_feedback": true,
  "sla_amber_warning": true,
  "sla_red_critical_alert": true,
  "sla_breached": true,
  "kyc_submission_alert": true,
  "account_deletion_detection_alert": true,
  "mie_recommendation_alert": true,
  "mie_pipeline_alert": true,
  "in_app_enabled": true,
  "course_update": true,
  "sla_amber_threshold_hours_override": null,
  "sla_red_threshold_hours_override": null
}
```

The PATCH response has the same shape.

### Design element → field

| Section | Design element | Field | What to do |
|---|---|---|---|
| Notification channels | Email Notifications | — | **Show disabled ("coming soon").** There's no email preference. Every email the platform sends today is transactional (withdrawal codes and outcomes, account and security emails), and those must not be switchable. |
| Notification channels | IN-app Notifications | `in_app_enabled` | ✅ Off stops every in-app notification except the critical notices listed at the end of Part 3. |
| System | APE daily production summary | — | **Show disabled.** Nothing produces this summary yet. |
| System | Provider failover alert | — | **Show disabled.** Nothing detects provider outages yet. (The design already greys this row.) |
| System | Review SLA Breach Alert | `sla_red_critical_alert` | ✅ The "Course overdue for review" alert. Use this field, not `sla_breached`. |
| System | Multi-account Fraud Cluster Detection | — | **Show disabled.** No fraud detection exists yet. |
| System | MIE Daily Proposal Batch | `mie_recommendation_alert` | **Show disabled.** The field saves, but nothing sends this alert yet. |
| Preference | Course Update | `course_update` | ✅ New. See Part 5. |

Fields the API returns that aren't on the admin design:

| Field | Status |
|---|---|
| `new_course_assigned` | ✅ Reviewers: "Course assigned to you" |
| `creator_feedback` | ✅ Appeal deciders: "New course-rejection appeal" (now enforced) |
| `kyc_submission_alert` | ✅ Account approvers: new KYC submission |
| `escalation_assigned`, `sla_amber_warning`, `sla_breached`, `account_deletion_detection_alert` | ❌ Saved but no sender exists. Don't show them as working toggles. |
| `mie_pipeline_alert` | ✅ Production Engine alerts: a run blocked by the budget, or failed. Sent to holders of `production.manage` (Admin, Super Admin). |
| `sla_amber_threshold_hours_override`, `sla_red_threshold_hours_override` | Reviewer-only overrides for review-queue ordering. Integer ≥ 1, or `null` for the platform default. |

> "Show disabled" means the backend has nothing behind the toggle. Hiding
> the row works just as well; your call with design. Either way, don't render
> it as a working switch.

---

## Part 5 — Course Update notifications

Who gets them: everyone holding `courses.assign` (Admin, Approver and Super
Admin by default) who hasn't switched `course_update` off, **except the
person who made the change**. Reviewers who aren't admin-tier don't get them.

| Event | `title` | `metadata.status` |
|---|---|---|
| A creator submits a course | `Course submitted` | `SUBMITTED` |
| A course passes QA | `Course approved` | `APPROVED` |
| A reviewer rejects at a content seat | `Course rejected` | `DRAFT` |
| QA rejects | `Course rejected in QA` | `DRAFT` |
| An admin publishes | `Course published` | `PUBLISHED` |

`metadata` is `{ "course_id": "<uuid>", "status": "<status>" }`. Use
`course_id` to link the row to the course. Moves between review seats (first
review → second review → verification → QA) aren't announced.

---

## Part 6 — Errors to branch on

| Endpoint | 400 | 401 | 404 |
|---|---|---|---|
| `GET notifications/` | — | no or expired token | — |
| `GET notifications/unread-count/` | — | no or expired token | — |
| `POST notifications/toggle-read/` | missing or invalid `notification_id` / `read_status` | no or expired token | not found, or not the caller's |
| `POST notifications/mark-all-read/` | — | no or expired token | — |
| `GET notifications/streamed-notifications/` | — | no or expired token (always, with native `EventSource`) | — |
| `PATCH notification-preferences/` | empty body, non-boolean toggle, override < 1 | no or expired token | — |

Error bodies use the standard shape:

```json
{
  "errors": [
    { "type": "client_error", "code": "not_found", "message": "…", "field_name": null }
  ]
}
```

---

## Part 7 — QA checklist

Run these against staging with two accounts: an **admin** and a **creator**
who has a submittable draft.

- [ ] Admin opens the app. The stream connects (200, `text/event-stream`) and
      the first event is an array.
- [ ] Creator submits the course. Without a refresh, the admin's list shows
      "Course submitted" at the top and the badge goes up by 1.
- [ ] The admin's Unread tab (`?is_read=false`) shows it. "Unread (n)"
      matches `unread-count`.
- [ ] Mark as read on that row: the dot clears and the badge goes down by 1.
      If the same admin has a second tab open, it updates too.
- [ ] Mark all as read: every dot clears and the badge shows 0. Pressing it
      again does nothing wrong.
- [ ] Admin switches Course Update off and saves. A second creator submission
      doesn't reach that admin.
- [ ] Admin switches In-app Notifications off. Ordinary notifications stop.
- [ ] Timestamps render correctly in **Safari** and show the user's timezone.
- [ ] Wait more than 30 minutes with the page open, then force a reconnect
      (toggle the network). The client refreshes its token and reconnects
      instead of looping on 401.
- [ ] Email, APE summary, Provider failover, Fraud detection and MIE Daily
      Proposal Batch can't be toggled.
- [ ] Empty account: the Notifications page shows the "No notifications"
      state.
