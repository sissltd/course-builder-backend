# Roles & Permissions and Admin Actions — Frontend Handover

Roles are now data. Admins can add roles and tick permission chips, and the
API enforces every chip. This covers the Roles & Permissions settings tab,
how the client should decide what to show, and the new admin actions behind
the design's chips: refunds, account deletion, password resets, invitations,
course assignment, version migration and the limited dashboard.

All paths are under `/api/v1/`. Full schemas are in Swagger under
**Admin — Roles & Permissions**, **Admin — Teams**, **Admin — Users**,
**Admin — Courses**, **Admin — Course Versions** and **Admin — Wallets**.

---

## Read first — behaviour changes

1. **`GET admin/roles/` has a new response shape.** It used to return a
   read-only catalogue per role. It now returns role cards (Part 2). The
   chip labels moved to `GET admin/permissions/`.
2. **`GET users/me/` gains `access_role` and `permissions`.** `permissions` is
   the list of codenames the user holds. Use it to decide which screens,
   buttons and chips to show. Don't infer access from `role` any more.
3. **Login may ask for MFA from users who enrolled voluntarily.** Where MFA is
   enforced (production), anyone with an MFA device now gets
   `{"mfa_required": true, "challenge_token": ...}` at login, not only Admins.
   Handle it with the existing verify screen.
4. **Money and identity actions need a session that passed MFA.** That covers
   role writes, change-role, account deletion and wallet adjustments. A
   session that didn't pass a challenge gets **403** with the message "This
   action needs a session verified with multi-factor authentication." Send
   the user through MFA enrolment or verification, then retry. MFA isn't
   enforced outside production, so this 403 won't appear there.
5. **In-app notifications for requests now go to whoever can act on them.**
   Category requests used to notify Admins and Approvers; they now notify
   holders of `catalog.manage_categories` (Writers, Admins and Super Admin by
   default).
6. **The dashboards can return null money figures.** `admin/overview/` and
   `admin/analytics/` have a new `financials_included` flag (Part 4).

Nothing else changed for existing callers. Every endpoint admits exactly the
same default roles as before.

---

## Part 1 — How access works now

- Every user holds one **role** (`access_role`). A role is a set of
  **permissions** (codenames such as `courses.approve`).
- There are **built-in roles**, one per platform role: Course Creator, Creator
  Reviewer, Writer, Verifier, Approver, AI Reviewer, QA Reviewer, Admin, Super
  Admin. There are also **custom roles** that admins create.
- Every custom role has a **base role**, which is always a staff role. The base
  role decides workflow, not permissions:
  - which review seats its members can sit
  - whether MFA is mandatory
  - which workspace they land in after login
- `users/me/` → `role` is that base role; `access_role` is the actual role.
- **Super Admin is locked.** It always holds every permission and can't be edited
  or deleted.
- **Permission changes take effect on the member's next request.** No
  sign-out, no token refresh needed. Refetch `users/me/` after a 403 you
  didn't expect.

### The design's chips → codenames

| Group | Chip | Codename | Default holders |
|---|---|---|---|
| Dashboard | View Only | `dashboard.view` | Admin, Super Admin |
| Dashboard | Limited Access | `dashboard.view_limited` | Super Admin |
| Courses | Approve Course | `courses.approve` | Creator Reviewer, Verifier, QA Reviewer, Approver, Admin, Super Admin |
| Courses | Reject Course | `courses.reject` | same as Approve |
| Courses | View Course | `courses.view` | Approver, Admin, Super Admin |
| Courses | Edit Course | `courses.edit` | Approver, Admin, Super Admin |
| Courses | Create Course | `courses.create` | Course Creator, Writer |
| Courses | Assign Course | `courses.assign` | Approver, Admin, Super Admin |
| Courses | Force Course Version Migration | `courses.force_version_migration` | Approver, Admin, Super Admin |
| Courses | Set Course Pricing at Approval | `courses.set_pricing` | Creator Reviewer, Verifier, Approver, Admin, Super Admin |
| Staff | Full Access | `staff.full_access` | Super Admin |
| Staff | View Only | `staff.view` | Super Admin |
| Staff | View Staff Detail | `staff.view_detail` | Super Admin |
| Staff | Delete Staff | `staff.delete` | Super Admin |
| Staff | Add Staff | `staff.add` | Super Admin |
| Staff | Reset password | `staff.reset_password` | Super Admin |
| Creators | View Wallet | `creators.view_wallet` | Admin, Super Admin |
| Creators | Suspend Account | `creators.suspend` | Admin, Super Admin |
| Creators | Issue Refund | `creators.issue_refund` | Super Admin |
| Creators | Approve Account (KYC) | `creators.approve_account` | Admin, Super Admin |
| Creators | View Profile | `creators.view_profile` | Admin, Super Admin |
| Teams | Invite Teams | `teams.invite` | Admin, Super Admin |
| Teams | Suspend Account | `teams.suspend` | Admin, Super Admin |
| Teams | Delete Account | `teams.delete_account` | Super Admin |
| Teams | Reset Password | `teams.reset_password` | Admin, Super Admin |
| APE Pipeline & MIE | View APE Pipeline | `mie.view_pipeline` | Admin, Super Admin |
| APE Pipeline & MIE | Approve MIE Topics Proposals | `mie.approve_topic_proposals` | Writer, Super Admin |

`GET admin/permissions/` also returns four groups the design doesn't show:
**Review pipeline**, **Catalog**, **Platform** and **Earnings**. They cover
publishing, appeals, categories, topics, platform settings, audit logs,
achievements, the roles screen itself (`roles.view`, `roles.manage`) and a
creator's own wallet. Render them below the design's groups, or behind a
"More" toggle. Without them, a custom role can't be given those abilities.

**Staff and Teams are the same group.** Both chip groups stay on the screen
exactly as before, but they act on the same accounts: every staff role
(Writer, Verifier, Approver, QA Reviewer, AI Reviewer, Creator Reviewer,
Admin) and creators. The only difference is that Admin and Super Admin
accounts can be acted on through the Staff chips alone, never the Teams
ones. Creator Reviewers now appear on the Teams page roster
(`GET auth/staff/`) and can be revoked, reactivated, invited or moved to
another role there, like any staff member. No request or response shape
changes.

---

## Part 2 — The Roles & Permissions tab

### Screen → endpoint

| Design element | Call | Who |
|---|---|---|
| Role cards (Super Admin, Admin, …) | `GET admin/roles/` | `roles.view` |
| Chip groups and labels | `GET admin/permissions/` | `roles.view` |
| Role tabs → highlighted chips | `permissions` on each role card | — |
| Add Role → Add new role → Save | `POST admin/roles/` | `roles.manage` + MFA session |
| Toggle chips → save | `PATCH admin/roles/{id}/` with the **full** `permissions` list | `roles.manage` + MFA session |
| Card ⋯ → Rename (custom roles) | `PATCH admin/roles/{id}/` `{name, description}` | `roles.manage` + MFA session |
| Card ⋯ → Delete (custom roles) | `DELETE admin/roles/{id}/?reassign_to_role_id=` | `roles.manage` + MFA session |
| Card ⋯ → Members | `GET admin/roles/{id}/members/?search=` (paginated) | `roles.view` + `staff.view` |

### Role card

```json
{
  "id": "0b6f8a9e-…",
  "name": "Content Lead",
  "description": "Writers who also curate categories.",
  "base_role": "STAFF_WRITER",
  "base_role_label": "Writer",
  "is_system": false,
  "is_locked": false,
  "is_deletable": true,
  "can_edit": true,
  "member_count": 3,
  "permissions": ["catalog.manage_categories", "courses.create", "earnings.manage_own"]
}
```

- **Disable all editing** when `can_edit` is false. That covers the Super Admin
  card, callers without `roles.manage`, and the caller's own role.
- **Disable individual chips** with `grantable_by_you: false` from
  `GET admin/permissions/`. A manager can only add or remove permissions they
  hold themselves; the Super Admin can grant everything.
- **Built-in roles:** hide Rename and Delete when `is_system` is true.
- **Course Creator and Creator Reviewer cards:** only chips with
  `grantable_to_public_roles: true` can be switched on. Anything else is 400,
  because every public sign-up would get it. Disable those chips on these two
  cards.

### Add new role

The design's dialog has only a title. The API also needs a **base role** (a
staff role picker) and the initial permissions:

```json
{ "name": "Content Lead", "description": "", "base_role": "STAFF_WRITER", "permissions": [] }
```

Recommended flow: create the role with an empty `permissions` list, then let
the admin tick chips on its new tab. The base role can't be changed later.

### Deleting a role with members

`DELETE` without `reassign_to_role_id` on a role that has members returns
**409** (`role_has_members`). Ask where members should go, offering roles with
the same `base_role`, then retry with `?reassign_to_role_id=`. Moved members
are signed out.

### Errors

| Status | When |
|---|---|
| 400 | Unknown codename; custom role with a non-staff base role; renaming a built-in role; admin-level chip on Course Creator or Creator Reviewer; empty PATCH; reassign target has a different base role |
| 403 | No `roles.manage`; granting or removing a permission you don't hold; editing your own role or the Super Admin role; deleting a built-in role; no MFA-verified session |
| 404 | Unknown or deleted role |
| 409 | Duplicate role name (case-insensitive); deleting a role that has members without `reassign_to_role_id` |

### Collaborators card and Reviewer 1 / Reviewer 2

- **Collaborators** isn't a platform role. It's per-course access, managed on the
  course. Don't show it as a role card.
- **First Review and Second Review aren't separate roles.** Both seats take a
  Creator Reviewer, and the same person can't decide both.

---

## Part 3 — Staff and account actions

| Action | Call | Who |
|---|---|---|
| Invite staff into any staff role (built-in or custom) | `POST auth/staff/invitations/` `{email, first_name, last_name, role_id}` | `staff.add` |
| Change a staff member's role | `POST auth/staff/{id}/change-role/` `{role_id}` | `staff.full_access` + MFA session |
| Send a staff member a password reset link | `POST auth/staff/{id}/send-password-reset/` | `staff.reset_password` |
| Delete a staff account | `POST auth/staff/{id}/erase/` `{reason, confirm_email}` | `staff.delete` + MFA session |
| Invite a Creator Reviewer (Invite Teams) | `POST users/admin/invitations/` `{email, first_name, last_name}` | `teams.invite` |
| Send a team member or creator a reset link (not an Admin) | `POST users/admin/{id}/send-password-reset/` | `teams.reset_password` |
| Delete a team member or creator account (not an Admin) | `POST users/admin/{id}/erase/` `{reason, confirm_email}` | `teams.delete_account` + MFA session |

**Invitations**
- The staff invite still accepts the old `role` value. Send exactly one of
  `role` or `role_id`.
- You may only invite into a role whose permissions you hold.
- Invited Creator Reviewers accept through the same link and
  `auth/staff/invitations/accept/` endpoint as staff.

**Change role**
- The member is signed out everywhere and notified.
- Review seats they had claimed but not decided, and that their new role can't
  sit, go back to the queue.

**Password reset**
- The password doesn't change until the user follows the link.
- Returns 400 for:
  - a pending invitee (resend the invite instead)
  - a suspended or deactivated account
  - a repeat within the resend cooldown
  - yourself

**Delete account** (irreversible)
- Signs the user out for good and erases their name, email, phone, address,
  KYC details, avatar, sign-in methods and saved bank accounts.
- Their courses, payouts, reviews and audit history stay, attributed to an
  anonymous "Deleted User".
- `confirm_email` must match the account's email. Make the admin type it.
- Returns **409** while the wallet has a balance or a payout is in progress.
- Reinstating or reactivating a deleted account is 400.

**Admins on the Teams routes.** An Admin or Super Admin id on a
`users/admin/...` route returns **404**. Every other account works on both
the `users/admin/...` and `auth/staff/...` routes.

---

## Part 4 — Course, dashboard and wallet actions

### Assign Course (`courses.assign`)

- **Assign dialog candidates:** `GET admin/courses/{id}/assignable-reviewers/`.
  - Returns reviewers who can sit the seat the course is waiting on: its content
    seat, or QA when it's in QA verification.
  - Each row has `is_available` and `holds_seat`.
  - Unavailable reviewers are listed; grey them out.
- **Assign:** `POST admin/courses/{id}/assign/` `{reviewer_id, replace}`. It
  works like the reviewer claiming the seat themselves, and they're notified.
- **Errors:**
  - **400:** the reviewer can't sit that seat (role, four-eyes, unavailable) or
    the course isn't in review
  - **409:** someone else holds the seat. Confirm with the admin, then retry
    with `replace: true`; the previous holder is notified.

### Force Course Version Migration (`courses.force_version_migration`)

- **List versions:** `GET admin/course-versions/`. Each row gives counts by status
  and `migratable_count`.
- **Migrate:** `POST admin/course-versions/migrations/`
  `{from_version_id, to_version_id, dry_run}`. Call with `dry_run: true`
  first and show `courses_moved` / `courses_by_status` in a confirmation,
  then repeat with `dry_run: false`.
- **Scope:** only unpublished courses move, Draft through Approved. Published
  courses keep their version.
- **Errors:** the target must be active and different from the source (400).

### Dashboard Limited Access

- **Who:** `admin/overview/`, `admin/analytics/` and `admin/system-health/`
  accept `dashboard.view` or `dashboard.view_limited`.
- **Limited Access response:** `financials_included` is **false** and these are
  **null**:
  - overview: `wallet_totals`, `withdrawals`, `cost_trend`,
    `today.daily_cost`, `today.daily_cost_change_percent`,
    `today.avg_cost_per_course`
  - analytics: `cost`, `earnings`, `kpis.cost_per_course`
- **Rendering:** hide those tiles and charts; don't show them as zero.

### Issue Refund (`creators.issue_refund`)

- **Adjust:** `POST admin/wallets/{wallet_id}/adjustments/`
  `{direction: "CREDIT"|"DEBIT", amount, reason, idempotency_key}`
  (MFA session).
- **Idempotency:** generate `idempotency_key` once per intended adjustment, e.g.
  a UUID when the dialog opens. A retry with the same key and payload returns
  the original with **200**; the same key with a different payload is **409**.
- **Errors:** a debit larger than the balance is **400** and nothing moves.
- **Notification:** the creator is notified, with the reason.
- **History:** `GET admin/wallet-adjustments/?user_id=&direction=` (paginated),
  for `creators.view_wallet` or `creators.issue_refund`.

---

## Open product questions

- **KYC date of birth is kept on deletion.** The model requires it, so a deleted
  account's KYC submissions keep their date of birth. Should it be retained, or
  replaced with a placeholder?
- **No per-adjustment limit on refunds.** Should there be a maximum per
  adjustment (e.g. a platform setting)?
- **Duplicate publish and pricing routes.** `courses/{id}/publish/` and
  `courses/{id}/review-prices/` are still stricter than their `review-queue`
  equivalents. Should the creator-side ones be retired?
