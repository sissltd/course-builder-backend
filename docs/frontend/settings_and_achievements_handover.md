# Admin Settings & Achievement Badges — Frontend Handover

This covers the admin **Settings** area (Account, Notifications, Permissions,
Platform, Payments, Achievement badge, Security) and the new **achievement
badges** feature. For each screen it says which endpoint feeds it, which
design elements have no backend yet (hide or stub them), and what changed.

All paths are under `/api/v1/`. Full request/response schemas are in Swagger
under the tags `Admin — Achievements`, `Creator — Achievements` and
`Admin — Roles & Permissions`.

---

## Read first — behaviour changes

1. **The in-app notifications toggle now works.** `in_app_enabled: false` on
   `PATCH users/me/notification-preferences/` used to be saved and ignored.
   It now stops new in-app notifications for that user. The exceptions, which
   still arrive: account suspended/reinstated notices, and the admin alerts
   for repeated login lockouts and the MIE circuit breaker.
2. **`GET users/me/` → `badges` is no longer always `[]`.** It lists the badges
   the user holds. Each entry keeps `code` (now the badge id) and `label` (the
   badge title), and adds `icon`, `color` and `awarded_at`.
3. **`GET users/me/` has a new `role_label`** (e.g. `"Writer"`), so the Account
   tab can show the role pill without mapping enum values. It also returns
   `access_role` and `permissions` — see the Roles & Permissions handover.
4. **Submitting a course can now fail with 400 on timing alone.** A course
   must sit as a draft for `draft_minimum_hold_hours` (ships at **48**) before
   `POST courses/{id}/submit/` will accept it. The message names the time it
   becomes submittable. This is new: submission previously failed only on
   structural grounds. It applies to first submission only — a course returned
   for revision resubmits immediately. Admins can set the value to `0` to
   switch the rule off entirely.
5. **A new in-app notification, "Course overdue for review".** Admins who can
   approve courses (and haven't switched off `sla_red_critical_alert`) get
   this once per course when it passes the SLA red threshold without a
   decision. `metadata` carries `course_id` and `hours_waiting`. Fires on a
   5-minute sweep, not instantly on the threshold tick.

Nothing was removed or renamed.

---

## Part 1 — Settings tabs

| Tab | Endpoint(s) | Notes |
|---|---|---|
| **Account** | `GET` / `PATCH users/me/` | Editable: `first_name`, `last_name`, `timezone` (IANA name), `avatar_url`. Upload the avatar first with `POST uploads/presign/` using `folder: "profiles"`, then PATCH the file key into `avatar_url`. `email` is read-only here; changing it is on the Security tab. Role pill: `role_label`. |
| **Notifications** | `GET` / `PATCH users/me/notification-preferences/` | See the field map below. |
| **Permissions** | `GET admin/roles/`, `GET admin/permissions/` and role writes | See Part 2. |
| **Platform** | `GET` / `PATCH platform-settings/` | PATCH needs `platform.edit_settings` (Admin and Super Admin by default) and an MFA-verified session. See the field map below. |
| **Payments** | Same `platform-settings/` | `payment_processor`: `PAYSTACK` or `FLUTTERWAVE`. |
| **Achievement badge** | `admin/achievements/badges/...` | See Part 3. |
| **Security** | `POST auth/change-email/` `{new_email, password}` → user confirms via emailed link (`POST auth/change-email/confirm/` `{token}`) · `POST auth/change-password/` `{current_password, new_password}` · `auth/mfa/*` · `GET auth/sessions/` | There is no `confirm_password` field, so check "Re-enter password" on the client. |

### Notifications — design element → field

| Design element | Field | Status |
|---|---|---|
| In-app Notifications | `in_app_enabled` | ✅ Enforced (see behaviour change 1) |
| Email Notifications | — | ❌ **Hide.** No email preference exists. The only emails the platform sends today are transactional (payouts, security), which should not be switchable. |
| Review SLA breach alert | `sla_red_critical_alert` | ✅ Now enforced. Switching it off opts that admin out of the review-overdue alert described under Platform below. `sla_amber_warning` and `sla_breached` remain saved but unread — no amber-tier or generic alert exists yet. |
| Provider failover alert | — | ❌ Hide. Nothing detects or sends it. |
| Multi-account fraud cluster detection | — | ❌ Hide. |
| MIE daily production summary | — | ❌ Hide. (`mie_pipeline_alert` exists but nothing sends it either.) |
| Daily financial alert | — | ❌ Hide. |
| Course update (Preferences) | — | ❌ Hide. |

### Platform — design element → field

| Design element | Field | Status |
|---|---|---|
| Topic reservation expiration | `topic_reservation_expiry_days` | ✅ One value, used for both draft auto-reservations and approved topic requests. The design shows two expiry rows; there is only one setting. |
| Review SLA thresholds | `sla_amber_threshold_hours`, `sla_red_threshold_hours` | ✅ Used to order the review queue. |
| Draft minimum hold time | `draft_minimum_hold_hours` | ✅ Hours a course must sit as a draft before it can be submitted. Default 48. `0` disables it. |
| Review SLA - admin alert | `sla_red_threshold_hours` | ✅ The same field the queue sorts by. Once a course passes it, admins are alerted **once**. |
| Flagging hour | `auto_flag_after_hours` | ✅ Default 48. `0` disables it. |
| Course structure limits (modules, lessons, words, duration, objectives, final-assessment questions), minimum withdrawal | `course_*`, `lesson_*`, `minimum_withdrawal_threshold` | ✅ Not in this design, but live on the same endpoint. |

**Draft minimum hold time** is counted from when the course was first created
and is never reset, so a course sent back for revision can be resubmitted
straight away — the hold is a cooling-off on new drafts, not on every
revision. Submitting too early returns **400** with a message naming the time
it becomes submittable.

**Flagging** is advisory: `flagged_at` and `flag_reason` appear on the admin
course rows, and `?flagged=true` filters to them (`?flagged=false` is the
inverse). A flag blocks nothing, changes no status and reassigns no reviewer.
It clears by itself once the course gets a decision or re-enters review.

> **No off switch for the SLA alert.** The design gives this row an hours
> input but no toggle, and the field behind it also drives review-queue
> ordering — so setting it to `0` would corrupt sorting rather than silence
> the alert. Today the alert can only be pushed further out, not turned off.
> Raised with product.

### Payments — design element → field

| Design element | Field | Status |
|---|---|---|
| Payment provider dropdown | `payment_processor` | ✅ |
| Saved provider account card | — | ❌ No platform provider account is modelled; provider credentials live in server config. Hide or show static text. |
| Payment auto-credit duration | — | ❌ Hide. Creators are credited immediately at QA approval; there is no hold period. |
| Creator verification toggle | — | ❌ Hide. KYC is always required before withdrawal and cannot be switched off. |

---

## Part 2 — Roles & Permissions

Roles and permissions are editable: Add Role, toggleable chips, member
lists and change-role are all supported. See
[roles_and_permissions_handover.md](roles_and_permissions_handover.md) for the
screen, the endpoints and what changed.

---

## Part 3 — Achievement badges

**Who:** holders of `achievements.manage` (Writer, Admin and Super Admin by
default) manage badges. Course Creators and Writers earn them.

### How earning works

Each badge has a `criterion`, a `required_count`, and an `auto_award` flag.

| `criterion` | Counts, per creator |
|---|---|
| `COURSES_CREATED` (default) | Courses they created, drafts included |
| `COURSES_REVIEWED` | Courses that passed all three content-review seats |
| `COURSES_APPROVED` | Courses that passed QA (published ones still count) |
| `COURSES_PUBLISHED` | Courses published |

- **`auto_award: true`**: creators earn the badge automatically **shortly
  after** the course event that takes them over the line. It happens in the
  background, not in that request, so refetch rather than expecting it in the
  response. Turning it on, or lowering `required_count`, also awards the
  badge straight away to every creator who already qualifies.
- **`auto_award: false`**: staff award it by hand (see Holders below).
- The creator gets an in-app notification when they earn a badge.
- Raising `required_count` **never** takes a badge away.

> **Design gap:** the Add new badge modal has no criterion picker, and its
> copy says "number of created courses". If you ship it as designed, omit
> `criterion` and it defaults to `COURSES_CREATED`. To support the other three,
> add a select; `criterion` can't be changed after creation.

### Screen → endpoint

| Design element | Call |
|---|---|
| Badge list | `GET admin/achievements/badges/` (paginated: `data.results`, `data.paginator`; `?page=`, `?size=`) |
| Row subtitle ("For creators who have …") | `requirement_summary` on each row. Don't build it client-side. |
| Achievement Analytics cards ("203 Creators") | `holder_count` on the same rows. There is no separate call. |
| Add new badge → Add badge | `POST admin/achievements/badges/` |
| ⋯ → Edit | `PATCH admin/achievements/badges/{id}/` with `title` / `icon` / `color` |
| ⋯ → Configure → Save | `PATCH admin/achievements/badges/{id}/` with `{ "required_count": 23 }` |
| ⋯ → Delete (dialog opens) | `GET admin/achievements/badges/{id}/deletion-impact/` |
| Delete badge button | `DELETE admin/achievements/badges/{id}/?move_to_previous=true|false` |
| (Optional) who holds a badge | `GET admin/achievements/badges/{id}/holders/` |
| (Optional) award by hand | `POST admin/achievements/badges/{id}/holders/` `{ "creator_id": "<uuid>" }` |
| (Optional) revoke | `DELETE admin/achievements/badges/{id}/holders/{creator_id}/` |

### Create body

```json
{
  "title": "Top",
  "icon": "diamond",
  "color": "#F2994A",
  "criterion": "COURSES_CREATED",
  "required_count": 100,
  "auto_award": true
}
```

- `icon` is a free-text key from **your** icon set (e.g. `diamond`, `medal`,
  `shield`). The API stores it without checking.
- `color` must be `#RRGGBB`.
- `required_count` must be at least 1.

### Delete dialog

- Call `deletion-impact` when the dialog opens. If `previous_badge` is `null`,
  **disable** "Move creators having this badge to the previous badge": there
  is no lower badge on that criterion.
- "Previous badge" means the badge with the same `criterion` and the next
  lower `required_count`.
- Sending `move_to_previous=true` when there is no previous badge returns
  **400**, and nothing is deleted.
- The response reports `holders_removed`, `holders_moved` and
  `moved_to_badge`. Creators who already held the previous badge aren't
  counted in `holders_moved`.

### Errors to branch on

| Status | When |
|---|---|
| 400 | Invalid field (bad colour, count < 1, unknown criterion), empty PATCH, or move with no previous badge |
| 403 | Caller isn't Writer, Admin or Super Admin (creator endpoint: isn't Course Creator or Writer) |
| 404 | Unknown or deleted badge; manual award to a user who isn't a Course Creator or Writer; revoking a badge the creator doesn't hold |
| 409 | Title already used by a live badge (case-insensitive); another badge already uses the same criterion + count; manual award to a creator who already holds it |

### Creator side

- `GET creator/achievements/` (Course Creator or Writer) returns every badge
  with `earned`, `awarded_at` and `current_count`, for a progress view.
- `current_count` can be above `required_count`. It can also be below it on
  an earned badge whose requirement was raised later, so trust `earned`.
- Held badges also appear on `GET users/me/` under `badges`.

---

## Open product questions

- **Farming "created" badges.** Drafts count and can be deleted, and earned
  badges are kept. A creator could create and delete drafts to earn one.
  Counting only submitted courses would close this.
- **The fields marked ❌ above** need backend work before they can do
  anything: email preferences, the alert engines, auto-credit hold, the
  optional-KYC toggle, draft topic limit and flagging rules.
