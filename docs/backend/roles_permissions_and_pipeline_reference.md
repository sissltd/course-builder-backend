# Roles, Permissions & Course Pipeline — Reference

> **Scope.** Everything needed to work on access control or the course
> pipeline without re-deriving it from scratch: every role, every permission
> class and what it admits, every endpoint's gate, the ownership-scoping
> patterns layered on top of role gates, and a full trace of the pipeline
> from an MIE idea through authoring, review, QA, publish and payout —
> including exactly where the implementation stops. Every claim below was
> checked against the source at the time of writing; line numbers can drift,
> so re-grep before trusting one in a stale-looking area.
>
> This is a technical reference for whoever (human or agent) touches this
> code next, not user-facing documentation. The product-facing version of
> the roles material lives at `docs/product/admin_roles_and_permissions.md`;
> this file is the evidence behind it, plus the pipeline material that
> doesn't belong in a product doc.

---

## 1. Role inventory

`api/users/enums.py::UserRole` — nine values. No user holds more than one.

| Role | Label | How obtained |
|---|---|---|
| `COURSE_CREATOR` | Course Creator | Public self-signup (`POST /auth/signup/`, default `signup_role`) |
| `CREATOR_REVIEWER` | Creator Reviewer | Public self-signup — a **dedicated** endpoint, `ReviewerSignupView` (`api/authentication/views/auth_views.py:170`), forcing `role=CREATOR_REVIEWER`. Also open, like Course Creator. |
| `STAFF_WRITER` | Writer | Super Admin invite only |
| `STAFF_VERIFIER` | Verifier | Super Admin invite only |
| `STAFF_APPROVER` | Approver | Super Admin invite only |
| `AI_REVIEWER` | AI Reviewer | Super Admin invite only. **No view anywhere gates on `IsAiReviewerRole`** — the role exists, the permission class exists, nothing consumes it (`api/users/permissions.py:69-78`, docstring says so explicitly). |
| `QA_REVIEWER` | QA Reviewer | Super Admin invite only |
| `ADMIN` | Admin | **Super Admin invite** (`api/users/enums.py:61-67`, `INVITABLE_STAFF_ROLES`). This is new: before this session's work, `ADMIN` had no API path at all and could only be set via Django admin or a direct DB write. Admins are MFA-mandated (`api/authentication/services/mfa_service.py:37`), so an invited Admin enrols MFA the same way the Super Admin does. |
| `SUPER_ADMIN` | Super Admin | One-time bootstrap endpoint, env-gated (`SUPERADMIN_BOOTSTRAP_ENABLED`), DB-constrained to exactly one row (`api/authentication/views/staff_views.py:71-197`). Never invited. |

```python
# api/users/enums.py
INVITABLE_STAFF_ROLES = (
    STAFF_WRITER, STAFF_VERIFIER, STAFF_APPROVER,
    AI_REVIEWER, QA_REVIEWER, ADMIN,
)
STAFF_ROLES = INVITABLE_STAFF_ROLES + (SUPER_ADMIN,)   # the Teams-page roster
```

MIE developers (external partners, the crawler) are **not** platform users
and hold no `UserRole` at all — they authenticate via API key or a
developer-scoped JWT into `request.auth`, and `request.user` stays
anonymous. See §6.9.

---

## 2. Permission primitives — how a gate actually decides

> **Changed.** Access used to be decided by role classes (`HasRole` subclasses
> such as `IsAdminRole`, plus `require_role`). Those were removed. Access is
> now decided by **stored permissions on roles**. The role tables in §6 still
> describe who holds each capability **by default**, because the built-in
> roles were seeded to reproduce them exactly. Admins can change that on the
> Roles & Permissions screen.

**Data model** (`api/authorization/models/role.py`):

- `Role(name, description, base_role, system_key)`. There is one built-in row
  per `UserRole` (`system_key` set), and custom roles have `system_key=NULL`.
- `RolePermission(role, codename)` stores the grants.
- `User.access_role` (FK, required) is the role whose permissions a user holds.
  `User.role` is the role's `base_role`, i.e. the *workflow* role.
  `User.save()` points `access_role` at the built-in role whenever `role` changes
  without an explicit access role.

**Registry** (`api/authorization/registry.py`, constants in `codenames.py`):

- It is the only list of codenames: labels, groups, `implies`,
  `grantable_to_public_roles` and `SYSTEM_ROLE_DEFAULT_GRANTS`.
- Stored codenames the registry doesn't know are ignored.
- A drift test (`SystemRoleSeedDriftTests`) fails if the seeded grants differ
  from the registry defaults. **A new codename needs a backfill migration**
  (see `authorization/0004`).

**Resolution** (`api/authorization/services/permission_service.py`):

- Superusers and `role == SUPER_ADMIN` hold `ALL_CODENAMES` with no query.
- Anyone else costs **one query per request**. The set is memoised for that
  request only (`apps.py` opens and closes the memo on Django's request
  signals).
- There is no cross-request cache, so a grant change applies on the next
  request. Celery and management commands have no memo and read fresh each
  time.
- Service checks: `require_permission`, `require_any_permission`,
  `user_has_permission`.
- Notification recipients: `users_with_permission(codename)`.

**DRF gates** (`api/authorization/permissions.py`):

- `Perm(*codenames)` builds a `HasPermission` class admitting holders of any
  of the codenames. It composes with `&` / `|` like any DRF permission, e.g.
  `(Perm(COURSES_CREATE) & IsCourseOwner) | Perm(COURSES_EDIT)`.
- `IsStrongMFASession` requires the token claim `mfa_challenged` (set only by
  the MFA verify flow) and a `role` claim equal to `user.role`. It's used on
  role writes, change-role, erase and wallet adjustments.
- `api/users/permissions.py::IsMFAVerifiedForSession` is unchanged, except that
  it now also rejects a token minted under a different role.

**Workflow checks that stay on `User.role`** (not permissions):

- `review_service.SEAT_ROLES` / `QA_SEAT_ROLES`, and four-eyes
- `mfa_service.MFA_MANDATED_ROLES`
- `STAFF_ROLES` / `INVITABLE_STAFF_ROLES`
- login workspace routing, signup roles
- `api/users/workflow.py` (`EARNING_ROLES`, `REVIEWER_WORKSPACE_ROLES`,
  `require_base_role`)
- Seat takeover by an admin (`review_service.is_admin_tier`) now means "holds
  `courses.assign`".

**Guard tests** (`api/authorization/tests/`):

- `parity/`: the gate matrix baseline from before the conversion, plus
  row-visibility and object-level tests. Declared deltas:
  - D1: Creator Reviewer and Verifier pass the QA view gates and are refused by
    the service.
  - D2: bank-account visibility gained the superuser bypass.
  - D3: a `SUPER_ADMIN` row without `is_superuser` holds everything.
- `test_no_role_gates.py`: role-class gates can't come back.
- `test_gate_coverage.py`: every handler checks a permission or is on the
  self-service allow-list.

---

## 3. Permission catalogue

See `registry.py` for labels, and `docs/frontend/roles_and_permissions_handover.md`
for the chip → codename table with default holders. Which codename each
endpoint checks is stated in that endpoint's Swagger `**Auth:**` line.

---

> **Reading §4–§6 after the permissions conversion.** These sections still
> name the role classes the gates used before. Default holders are unchanged,
> so the tables still say who can do what out of the box. Each class was
> replaced by a permission, which depends on the endpoint:
>
> | Old gate | Now (per endpoint) |
> |---|---|
> | `IsCourseCreatorRole` | `courses.create` (authoring), `earnings.manage_own` (own wallet) |
> | `IsAdminRole` on courses | `courses.view` / `courses.edit`; seat takeover `courses.assign` |
> | `IsCreatorReviewerRole \| IsQaReviewerRole \| IsAdminRole` (review queue) | `courses.approve` / `courses.reject` (+ `courses.view` for reads); seat eligibility stays on `User.role` |
> | review-route `review_prices` / `publish` | `courses.set_pricing` / `courses.publish` |
> | `IsAdminOrSuperAdminRole` | the specific capability: `dashboard.view`, `audit.view`, `platform.edit_settings`, `creators.*`, `courses.decide_appeals`, `courses.manage_quality`, `reviewers.assign_track`, `roles.view` |
> | `CanManageCategories` | `catalog.manage_categories` |
> | `IsAdminRole \| IsCreatorReviewerRole` on topics | `catalog.manage_topics`; admin reservation screens `catalog.view_topic_queue` |
> | `CanManageAchievements` | `achievements.manage` |
> | `CanDecideMieIdeas` | `mie.approve_topic_proposals` |
> | `IsSuperAdminRole` | `staff.*` (staff screens, MFA reset) or `mie.manage_console` |
> | `OWNER_OR_ADMIN` | `OWNER_OR_VIEWER` (retrieve) / `OWNER_OR_EDITOR` (update, delete) |

## 4. Ownership-scoping helpers (a role gate is not a tenant check)

A role class proves *what* a caller may do — never *which row*. Every
resource that isn't globally admin-visible layers one of these on top:

| Pattern | Where | Mechanic |
|---|---|---|
| `OWNER_OR_ADMIN = (IsCourseCreatorRole & IsCourseOwner) | IsAdminRole` | `course_views.py:59` | `IsCourseOwner` (same file) checks `obj.creator_id == request.user.id`. Used for `retrieve/update/partial_update/destroy` on a course (`OWNER_SCOPED_ACTIONS`, `:54`). |
| `SUBMIT_PERMISSION = (IsCourseCreatorRole | IsAdminRole) & IsCourseOwner` | `course_views.py:65` | Submitting requires ownership even for the admin tier — an Admin cannot submit someone else's draft. |
| `get_courses_accessible_to(user)` | `api/collaborators/services/collaborator_service.py` | Collaborator-scoped read/write: an invited collaborator (ADMIN-role or plain) sees courses they're added to, not just their own. Used for `retrieve/update/partial_update` when the caller isn't in the admin tier (`course_views.py:578-581`). |
| Queryset filter by `requested_by=self.request.user` unless admin-tier | `category_request_views.py:66-70`, `topic_reservation_views.py:255-260`, `course_appeal_views.py:151-156` | The standard "creator sees own, admin tier sees all" pattern — repeated per app rather than shared. |
| Seat-and-claim lock on `CourseSubmission`/review actions | see §6.4 | Not ownership in the usual sense — a *claim* a reviewer must hold before deciding. |

**Rule of thumb when adding a new resource:** put the tenant/owner filter in
the query (`.filter(creator=...)` or an explicit ownership check), never as
a post-fetch `if obj.owner != request.user: raise`. A foreign object should
404, not 403 — 403 confirms the id exists, turning the endpoint into an
enumeration oracle. All the patterns above follow this; check any new one
against it.

---

## 5. MFA

`IsMFAVerifiedForSession` gates, composed with a role class:

| Surface | Gate |
|---|---|
| `PATCH /platform/settings/` | `IsAdminOrSuperAdminRole & IsMFAVerifiedForSession` (`platform/views.py:65-68`) |
| Category create/update/delete/archive/unarchive | `CanManageCategories & IsMFAVerifiedForSession` (`category_views.py:455-457`) |

`MFA_MANDATED_ROLES = (ADMIN, SUPER_ADMIN)` only. Approver, Writer, Verifier,
QA Reviewer, AI Reviewer are never MFA-mandated even though some of them can
reach admin-tier-gated endpoints (Approver via `IsAdminRole`).

---

## 6. Capability matrix by domain

Legend: **CC**=Course Creator, **CR**=Creator Reviewer, **W**=Writer,
**V**=Verifier, **AP**=Approver, **QA**=QA Reviewer, **AI**=AI Reviewer
(unused), **AD**=Admin, **SA**=Super Admin. A blank cell = refused (401/403).

### 6.1 Auth, staff, identity

| Action | Endpoint | Gate |
|---|---|---|
| Signup (Course Creator) | `POST /auth/signup/` | Public |
| Signup (Creator Reviewer) | `POST /auth/signup/reviewer/` | Public |
| Superadmin bootstrap | `POST /auth/superadmin/bootstrap/` | Public, env-gated, one-time |
| Superadmin bootstrap | `POST /auth/superadmin/bootstrap/` | `AllowAny` (`SuperAdminBootstrapView`, `staff_views.py:71-75`), env-gated + DB-constrained to one row |
| Invite staff (incl. now Admin), list/detail staff | `InviteStaffView`, `StaffListView`, `StaffDetailView` | `IsSuperAdminRole` (`staff_views.py:203,252,283`) |
| Revoke / reactivate staff | `RevokeStaffView`, `ReactivateStaffView` | `IsSuperAdminRole` (`:431,533`) |
| Accept a staff invitation | `AcceptStaffInvitationView` | `AllowAny` (`:635-639`) — necessarily public; the invitee has no account yet |
| Reset another user's MFA | `POST /auth/mfa/admin/reset/` | `IsSuperAdminRole` (`mfa_views.py:320`) |
| KYC review (approve/reject) | `api/users/views/kyc_views.py:170,450` | `IsAdminOrSuperAdminRole` (**not** Approver) |
| Own KYC submission / liveness | `kyc_views.py:59,285` | `IsAuthenticated` |
| User admin (suspend/deactivate) | `user_admin_views.py:149` | `IsAdminOrSuperAdminRole` |
| Platform-wide audit log | `GET /logs` | `IsAdminOrSuperAdminRole` — **changed this session**, previously Django `IsAdminUser` (`is_staff`), which only the bootstrap account had |
| Own audit trail export | `GET /users/me/audit-log/export/` | `IsAuthenticated`, self-scoped by email |

### 6.2 Course authoring (draft → submit)

All under `IsCourseCreatorRole` (CC, W) at the class level, narrowed per
action:

| Action | Gate |
|---|---|
| List (own) / Create | `IsCourseCreatorRole` |
| Retrieve / Update / Delete | `OWNER_OR_ADMIN` — owner or admin tier |
| Submit for review | `SUBMIT_PERMISSION` — owner (creator or admin tier) only, never a non-owning admin |
| Modules, Lessons, Assessments, Quizzes, Lesson sub-resources, Course import, Course thumbnail | all `IsCourseCreatorRole` at the view (`assessment_views.py`, `module_views.py`, `lesson_views.py`, `lesson_sub_resource_views.py`, `course_import_views.py`, `course_thumbnail_views.py`, `quiz_views.py`, `question_views.py` — the last two also admit `IsAdminRole`) |
| Register a media asset | `POST/GET /courses/{id}/media-assets/` — `IsCourseCreatorRole | IsAdminRole` at the class (`course_views.py:551`), but **ownership comes only from the queryset** (`.filter(creator=self.request.user)` for non-admins, `:584-586`) since `media_assets` is not in `OWNER_SCOPED_ACTIONS`. A collaborator Writer added to someone else's course **cannot** register media — only `retrieve/update/partial_update` use collaborator scoping. |
| Upload file bytes (presign / access) | `shared/uploads/views.py:29,54` — `IsAuthenticated` only. **Any logged-in user, any course, no ownership check at all.** Not tied to `media-assets` registration in any way — see §7.2. |
| Course appeals (file one) | `IsCourseCreatorRole | IsAdminOrSuperAdminRole`, self-scoped unless admin tier |
| Course appeals (approve/reject) | `get_permissions()` narrows to `IsAdminOrSuperAdminRole` only (`course_appeal_views.py:164-166`) — Approver **cannot** decide an appeal even though it can review courses |
| Topic reservation requests (approve/reject) | `IsAdminRole | IsCreatorReviewerRole` (`topic_reservation_views.py:268-270`) |

### 6.3 AI generation

All five endpoints (`ai_generation_views.py:111,277,359,402,486,570,639`) are
`IsCourseCreatorRole` — CC and W only, nobody else, no admin override. One
in-flight job per creator (`ai_generation_service.py:330-339`). Produces
real `Module`/`Lesson`/`Assessment` rows and thumbnail images via OpenAI's
Responses API (not stubbed) — but **text and images only, never
video/audio**. See §7.3.

### 6.4 Content review chain (three seats)

Introduced this workstream — see `docs/frontend/content_review_chain_handover.md`
for the full contract. Summary:

| Seat | `review_stage` | Who may hold it |
|---|---|---|
| First Review | `CONTENT` | `CREATOR_REVIEWER` |
| Second Review | `SECOND_REVIEW` | `CREATOR_REVIEWER` — **a different person than First Review** (four-eyes, enforced in `review_service.py`) |
| Verification | `VERIFICATION` | `STAFF_VERIFIER` |

Admin tier (`AD`, `AP`, `SA`) may take any seat, or override one someone
else holds — logged via `log_activity`. A reviewer must **claim** a seat
(row-locked, exclusive) before approve/reject; deciding an unclaimed seat is
400, deciding someone else's claim is 403 unless you're admin tier. Any
rejection returns the course to `DRAFT` and clears every seat; resubmission
restarts at First Review.

View gate: `CourseReviewViewSet.get_permissions()` (`course_views.py:1224-1229`)
— `claim/approve/reject/content_approve/content_reject` fall through to the
class default `IsCreatorReviewerRole | IsQaReviewerRole | IsAdminRole`
(`:1119`), then `review_service.py` re-checks the seat-specific role via
`require_role`.

### 6.5 QA verification

| Action | Gate |
|---|---|
| `qa_claim`, `qa_approve`, `qa_reject` | `IsQaReviewerRole | IsAdminRole` (`course_views.py:1225-1226`), re-checked in `review_service.py:463,486,546` |
| `review_prices`, `publish` (on the **reviewer/admin** viewset) | `IsCreatorReviewerRole | IsAdminRole` (`:1227-1228`) — note CR/V can publish here |
| `review_prices`, `publish` (on the **creator-facing** `CourseViewSet`) | `IsAdminRole` only (`:600-601`) — a **stricter** gate on a different route to the same action |

`approve_qa` is where video actually gets checked — see §7.4. It is the
**only** point in the entire pipeline that inspects `MediaAsset` rows.

### 6.6 Publish & distribution

Two different gates reach `publish_course`, and they disagree:

- `CourseViewSet.publish` (creator-facing route): `IsAdminRole` only.
- `CourseReviewViewSet.publish` / `AdminCourseViewSet.publish` (reviewer
  route): `IsCreatorReviewerRole | IsAdminRole`.

The **service** (`course_service.py:435-438`) uses
`require_role(actor, IsCreatorReviewerRole.allowed_roles +
IsAdminRole.allowed_roles)` — i.e. it agrees with the *wider* reviewer-route
gate, so there's no service-level backstop closing the creator-route's
stricter gate. Net effect: **CR and V can publish**, via the reviewer route,
even though the creator-facing route alone would suggest only the admin
tier can. Flagged, not fixed — worth a product decision.

Publish writes: course → `PUBLISHED` + a `PublishedCourseSnapshot`; **every**
`CourseDistribution` channel → `QUEUED`; **SOLUDESK is then immediately
overwritten to `PUBLISHED`**. UDEMY/COURSERA rows sit at `QUEUED` forever —
no worker, no beat task, no consumer reads them. `external_course_id` is
never written by anything.

### 6.7 Wallet & payments

| Action | Gate |
|---|---|
| View own wallet, request/confirm withdrawal | `IsCourseCreatorRole` (`wallet/views.py:61,100,108`) — CC, W |
| Admin wallet/transaction/withdrawal lists | `IsAdminOrSuperAdminRole` (`:186,257,328`) — **read-only**, no admin action exists to retry/cancel/settle a payout |
| Bank account list/create/detail/default | `IsAuthenticated` (self-scoped in the service) |
| **Bank account suspend** | `IsAdminOrSuperAdminRole` — **changed this session**, was `IsAdminRole` (Approver could suspend any user's payout account; fixed, and the activity log now records it on the account owner, not the admin) |
| Transaction history (own) | `IsCourseCreatorRole` (`transaction_views.py:21`) |

### 6.8 Catalog (categories, topics, requests)

| Action | Gate |
|---|---|
| Category read | any authenticated (public browse) |
| Category create/update/delete/archive/unarchive | `CanManageCategories & IsMFAVerifiedForSession` |
| Category counts, deletion-impact, picker (admin reads) | `CanManageCategories` |
| Category **requests** (file) | `IsCourseCreatorRole | CanManageCategories` |
| Category requests (approve/reject) | `get_permissions()` narrows to `CanManageCategories()` (`category_request_views.py:80-81`) — **changed this session**: previously the view admitted Approver (`IsAdminRole`) while the service required `CanManageCategories`, so an Approver passed the gate and was then 403'd by the service, and a Writer was blocked at the gate despite being allowed by the service. Now one rule everywhere. |
| Topic write / release-reservation | `IsAdminRole | IsCreatorReviewerRole` (`topic_views.py:240-243`) |
| Topic reservation requests (manage) | `IsAdminRole | IsCreatorReviewerRole` |
| Quality-check criteria template (CRUD) | create/update/destroy → `IsAdminOrSuperAdminRole`; read → `IsCourseCreatorRole | IsAdminOrSuperAdminRole` (`reviews/views.py:72-75`) |
| Course quality-check refresh (GET/POST) | `IsCourseCreatorRole | IsAdminOrSuperAdminRole` (`reviews/views.py:97`) |

### 6.9 MIE (developer-facing + admin)

Developers are **not platform users** — no `UserRole`. `MieDeveloperAuthentication`
resolves an `X-MIE-Api-Key` header or a developer-scoped Bearer token to a
`DeveloperAccount`, stored as `request.auth`; `request.user` stays
anonymous. `IsMieDeveloper` checks `isinstance(request.auth,
DeveloperAccount) and status == APPROVED`.

| Surface | Gate |
|---|---|
| Register, submit idea, own queue, `/me`, docs | `IsMieDeveloper` (API key or dev Bearer) |
| MIE admin console (developers, submission decisions via `/mie/admin/...`, rejection-reason taxonomy) | `IsSuperAdminRole` — **unchanged**, still Super Admin only |
| **Recommendations screen** (`/api/v1/admin/mie-recommendations/...` — list, approve, reject, bulk decide) | `CanDecideMieIdeas` = `STAFF_WRITER, SUPER_ADMIN` — **new this session**. A plain `ADMIN` gets 403 on this screen even though Admin can do almost everything else, because the screen carries decision buttons and curating the idea queue was made the Writer's job. |
| MIE ingest rate limit | now **per developer account** (whichever credential presented), not per IP — `api/mie/throttling.py`, new this session |
| MIE registration rate limit | still per client IP (no account exists yet) |

Two separate decision surfaces exist for the same `CourseSubmission`:
the Super-Admin-only console (`/mie/admin/submissions/{id}/approve|reject/`)
and the Writer-reachable Recommendations screen
(`/admin/mie-recommendations/{id}/approve|reject/`, plus a bulk route). Both
ultimately call the same `submission_admin_service.decide_submission` /
`decide_submissions_bulk`, gated by `CanDecideMieIdeas` inside the service
too (`require_role`).

### 6.10 Operations / dashboards / audit

| Surface | Gate |
|---|---|
| Analytics, system health, pipeline overview | `IsAuthenticated, IsAdminOrSuperAdminRole` (`operations/views.py`) |
| MIE Recommendations | see §6.9 — narrower than the rest of `operations` |
| Reviewer/Creator overview | `IsAuthenticated` at the view; `require_role` inside the service (defense-in-depth pattern, weaker at the view layer than peers) |

### 6.11 Achievements, roles and admin actions

| Surface | Gate |
|---|---|
| Badge list/create/retrieve/update/delete, deletion impact, holders list, manual award, revoke (`/api/v1/admin/achievements/badges/...`) | `CanManageAchievements` |
| Own badges and progress (`GET /api/v1/creator/achievements/`) | `IsCourseCreatorRole` |
| Roles & Permissions catalogue (`GET /api/v1/admin/roles/`) | `IsAdminOrSuperAdminRole` |

Only earning roles (`IsCourseCreatorRole.allowed_roles`) can hold a badge;
manual award to any other user 404s. Automatic awards are queued from the
lifecycle with `transaction.on_commit` → Celery
(`award_service.schedule_evaluation`), at `create_draft_course` (created),
`approve_content`'s last seat (passed content review), `approve_qa`
(approved) and `publish_course` (published). The legacy `approve_course` is
not hooked (tests-only). Counts are defined once in
`api/achievements/services/criterion_service.py::COUNT_EXPRESSIONS`.

Roles & Permissions management (`role_admin_service`): `admin/roles/` (list,
create, retrieve, update, delete with reassignment, members) and
`admin/permissions/`, plus `auth/staff/{id}/change-role/`. Admin actions added
with it:

- `auth/staff|users/admin/{id}/send-password-reset/` (`account_admin_service`)
- `.../erase/` (`account_erasure_service`)
- `users/admin/invitations/`
- `admin/courses/{id}/assign/` and `assignable-reviewers/` (`review_service.assign_seat`)
- `admin/course-versions/` and `migrations/` (`course_version_service`)
- `admin/wallets/{id}/adjustments/` and `admin/wallet-adjustments/` (`wallet_adjustment_service`)
- Limited Access on the dashboards (`include_financials`)

---

## 7. The pipeline, end to end — what's built and where it stops

### 7.1 MIE idea → course: **the bridge does not exist**

When a Writer or Super Admin approves an MIE idea, the **only** thing that
happens is a webhook fired to the external developer. Every reader of
`SubmissionStatus.APPROVED` lives *inside* the `api/mie` app (the webhook
event-type map, the circuit-breaker rejection tally, the docs generator, the
`SCB-...-A` reference-suffix map). `CourseSubmission.resulting_course` is
**never assigned anywhere** — confirmed by grep and by the code's own
comments (`submission_admin_service.py`: "nothing sets resulting_course
yet"). `CourseSourceType.DEVELOPER_API` is **never set anywhere** — it's a
label with no writer. MIE never creates a `Course` row.

**Practical consequence:** turning an approved idea into a real course is
today a fully manual, unmodelled step — someone reads the approved idea and
authors the course by hand through the ordinary creator flow (§7.2), with
no system link back to the `CourseSubmission` it came from, and no internal
notification that an idea was even approved (the webhook only reaches the
external developer).

### 7.2 Authoring → submit

Three ways to create a course (§6.2, §6.3): manual authoring, AI generation
(real content, no media), document import. The submit gate
(`quality_check_service.validate_structural_standards`) runs twelve
structural checks — objective/module/lesson counts, script word counts,
duration bounds, a final assessment — plus exactly **one** media-adjacent
rule: `course.preview_video_url` must be a non-empty string. That string is
**never validated or dereferenced**, and no `MediaAsset` row needs to exist
for a course to submit. `thumbnail_url` on `Course` is never checked here
either.

### 7.3 Content review → QA claim

Three-seat chain (§6.4) checks nothing media-related. `qa_claim` likewise
checks nothing media-related.

### 7.4 QA approval — the only real media/video gate

`quality_review_service.required_media_failures(course)` runs **inside**
`approve_qa`, **before** the transaction opens, so a failure writes nothing:

| Requirement | Scope |
|---|---|
| ≥1 `PREVIEW_VIDEO` asset | per course |
| ≥1 `THUMBNAIL` asset | per course |
| ≥1 `VIDEO` asset | **per lesson**, every lesson of every module |
| `mime_type`, `duration_seconds`, `resolution`, `subtitle_url`, `caption_accuracy_percent`, `audio_lufs`, `audio_video_drift_ms`, `accessibility` all present | per VIDEO/PREVIEW_VIDEO asset |

**With video:** the creator (or admin-tier caller, or collaborator with
write access — but *not* a collaborator Writer registering media, per
§6.2's note) calls `POST /courses/{id}/media-assets/` with a **URL string
plus metadata** — no bytes travel through this endpoint. Bytes go through
`shared/uploads/` (presign → S3 PUT → access), which is open to **any**
authenticated user regardless of course ownership, and is **not linked** to
registration in any way: nothing verifies a registered URL actually came
from the presign flow, so the codec/duration/resolution rules enforced only
at presign time (`storage_service.py` — h264, 60–120s preview duration,
1280×720 minimum, 16:9) can be bypassed entirely by registering an
arbitrary URL.

**Without video:** the course sails through authoring, submission, and all
three content-review seats, then **dead-ends at `qa_approve`** with a 400
listing every missing asset. It sits in `QA_VERIFICATION` indefinitely — the
only exits are registering the required assets, or a QA rejection back to
`DRAFT`. There is **no endpoint to preview these blockers in advance**; the
reviewer only discovers them by attempting the approval and reading the 400
body. `CourseDetailSerializer.media_assets` / `qa_video_samples` show
whatever *is* registered, but nothing proactively lists what's missing.

`MediaAsset.verified_by`/`verified_at` are modelled (a QA sign-off per
asset) but **never written by anything** — QA approval is recorded on
`ReviewAction`/`ReviewAssignment` instead, not per-asset.

### 7.5 Approval → wallet credit

`approve_qa` (and the dead/unused `approve_course`) call
`wallet_service.credit_wallet` with `amount=course.creator_price_snapshot`,
gated by `if course.source_type == CourseSourceType.CREATOR_UPLOADED`
**exactly** — not `!= AI_GENERATED`. Consequence, worth flagging as a likely
bug: `AI_GENERATED` courses correctly get no credit (their snapshot is
`None` anyway), but **`DOCUMENT_IMPORTED` courses get a price snapshot at
submit and then no credit at approval**, because the check is an equality
against one value rather than an exclusion of the AI case. If/when
`DEVELOPER_API` is ever wired up (§7.1), it would hit the same gap.

### 7.6 Publish (§6.6) → distribution

SOLUDESK publishes for real. UDEMY/COURSERA are permanently `QUEUED` with no
consumer. No unpublish path; `PublishedCourseSnapshot` is written once, never
versioned.

### 7.7 Money out — corrected from an earlier claim in this conversation

An earlier pass in this session said payout settlement was incomplete
because nothing marks a withdrawal COMPLETED/FAILED. **That was wrong**, and
traced back to stale docstrings in `wallet_service.py`. The real path is
built and functional: `request_withdrawal` → OTP `confirm_withdrawal` →
wallet debited into a suspense `InternalAccount`
(`transaction_services.internal_transfer`, writes real `COMPLETED`
transaction legs immediately) → `TransferOutboxEvent` created → Celery
`dispatch_transfer_task` calls a **real** Paystack/Flutterwave transfer →
webhook (`paystack_webhook_services.py`) handles `transfer.success` /
`transfer.failed` / `transfer.reversed`, reversing funds on failure.

What genuinely stops short: the **`WithdrawalRequest` row itself** only ever
reaches `CONFIRMED` — `WithdrawalRequestStatus` has no COMPLETED/FAILED, and
`EXPIRED` is declared but never assigned. Terminal state lives on
`TransferOutboxEvent.status` and the activity log instead. The three admin
wallet endpoints are read-only — **no admin action exists to retry, cancel,
or manually settle a stuck payout.**

### 7.8 Learners — nothing

`api/operations/models.py::Enrollment` is never instantiated outside test
factories. No checkout, cart, purchase, or learner-facing catalog exists
anywhere in the codebase (`api/catalog` is admin taxonomy management, not a
storefront). **Money only ever flows out to creators; nothing ever flows
in from a learner.**

### 7.9 Where the implementation ends

**A course can go from authoring to PUBLISHED on SOLUDESK, with its creator
paid and able to withdraw to a real bank transfer — provided it's
`CREATOR_UPLOADED` and carries every required video asset.** Everything
past that boundary (external channel distribution, learner purchase,
enrollment, the MIE-idea-to-course bridge, per-asset QA sign-off, AI-produced
video) is either a dead end, a manual gap, or entirely unbuilt.

---

## 8. Modelled but not implemented (full list, evidence-checked)

| Thing | Where | Evidence |
|---|---|---|
| `IsAiReviewerRole` / `AI_REVIEWER` review track | `permissions.py:69-78` | No view references it |
| `CourseSubmission.resulting_course` | `api/mie/models/course_submission.py` | Never assigned; §7.1 |
| `CourseSourceType.DEVELOPER_API` | `courses/enums.py` | Never assigned |
| `MediaAsset.verified_by` / `verified_at` | `reviews/models/review_quality.py` | Never written |
| `MediaAsset.verification` (JSON) | same | Client-writable, read by nothing |
| `AUDIO`, `SUBTITLE` `MediaAsset.kind` values | `reviews/enums.py` | Accepted on POST, never required/consumed by any check |
| `QualityCheckRun.plagiarism_*` / `duplicate_*` | `reviews/models/review_quality.py` | Left at `NOT_RUN` forever |
| `Course.thumbnail_url` validation | `courses/models/course.py` | Never checked by submit or QA gates |
| `CourseDistribution.external_course_id` | `courses/models/course_distribution.py` | Never written |
| `DistributionStatus.FAILED` | same enum | Never assigned |
| `Enrollment` / learner purchase | `operations/models.py` | §7.8 |
| `ProductionCost` | `operations/models.py` | Only read by analytics; nothing writes it in the traced flow |
| `WithdrawalRequestStatus.EXPIRED` | `wallet/enums.py` | Never assigned |
| Withdrawal terminal state on the request row | `wallet/services/wallet_service.py` | Stops at `CONFIRMED`; §7.7 |
| `CourseStatus.REJECTED` | `courses/enums.py` | Never persisted on `Course.status` — rejections go straight to `DRAFT` |
| `AIGenerationItem.target_type` / `target_id` | `courses/models/ai_generation.py` | Never written |
| Admin retry/cancel/settle for a stuck payout | `wallet/views.py` | All three admin wallet views are `ListAPIView` |

---

## 9. Recently fixed / changed (this workstream) — don't re-discover these

Everything below is **already done**, evidenced by the diffs in this
session. If you're picking this codebase up fresh, treat these as settled,
not as findings:

1. **API key lookup** now hashes and matches on the indexed
   `api_key_hash` column, not an unindexed prefix carrying only 7 random
   characters (`api/mie/services/key_service.py`).
2. **Content review** is now a three-seat chain (§6.4), not a single approval.
3. **MIE crawler** (`SYSTEM` source-type developer account) exists, with a
   daily submission cap and a rejection circuit-breaker, both binding
   `SYSTEM` accounts only.
4. **MIE Recommendations screen** (§6.9) is now a real working queue —
   paginated, filterable, with approve/reject/bulk-decide — not the
   read-only ranked list it started as. New submission fields: `category`
   (FK, resolved from submitted name/slug), `difficulty_level`,
   `searches_per_month`, `description`.
5. **Bank account suspension** narrowed to `IsAdminOrSuperAdminRole`
   (§6.7), was `IsAdminRole`.
6. **Audit log** narrowed to `IsAdminOrSuperAdminRole` (§6.1), was Django's
   `IsAdminUser`/`is_staff`.
7. **Category requests** unified onto one role set, `CanManageCategories`,
   across view/queryset/service (§6.8).
8. **`ADMIN` is now invitable** (§1) — previously had no API path at all.
9. **MIE ingest throttle** is now per developer account, not per IP
   (§6.9); `DRF_NUM_PROXIES` setting added so IP-keyed limits (signup,
   login, MIE registration) can read the real client IP behind a reverse
   proxy — inert until an environment sets it.
10. **Category search** no longer filters on `Category.description`
    (removed by a parallel catalog change); the search result's `subtitle`
    now falls back to the category name.
11. **Achievements app** (§6.11) and the **Roles & Permissions catalogue**
    added. `/users/me/` gained `role_label`, and `badges` now lists real awards
    (it returned `[]` before).
12. **`NotificationPreference.in_app_enabled` is enforced** in
    `Notification.emit_in_app_notification`, which previously ignored it.
    `critical=True` bypasses it: login-lockout and MIE circuit-breaker
    alerts, and account suspend/reinstate notices.

---

## 10. File index — where to look

| Concern | File |
|---|---|
| Role enum, invitable/staff lists | `api/users/enums.py` |
| All permission classes | `api/users/permissions.py` |
| MFA session-claim gate | `api/users/permissions.py::IsMFAVerifiedForSession`, `api/authentication/services/mfa_service.py` |
| Course ownership helpers | `api/courses/views/course_views.py` (top of file) |
| Collaborator scoping | `api/collaborators/services/collaborator_service.py` |
| Content review chain | `api/reviews/services/review_service.py`, `api/courses/services/course_service.py` |
| QA media gate | `api/reviews/services/quality_review_service.py::required_media_failures` |
| Submit gate | `api/reviews/services/quality_check_service.py::validate_structural_standards` |
| Upload limits/rules | `shared/services/storage_service.py` |
| AI generation | `api/courses/services/ai_generation_service.py`, `api/courses/ai/providers.py`, `api/courses/tasks.py` |
| Publish | `api/courses/services/course_service.py::publish_course` |
| Wallet / withdrawal | `api/wallet/services/wallet_service.py`, `api/wallet/tasks.py`, `api/payments/services/transaction_services.py` |
| MIE app (whole) | `api/mie/` — see also `docs/backend/mie_end_to_end.md` |
| MIE Recommendations | `api/operations/views.py`, `api/operations/filters.py`, `api/operations/services/recommendation_service.py` |
| Frontend contract for the review chain | `docs/frontend/content_review_chain_handover.md` |
| Frontend contract for the MIE crawler + recommendations | `docs/frontend/mie_crawler_frontend_handover.md`, `docs/frontend/FRONTEND_HANDOVER.md`, `docs/frontend/ADMIN_REVIEWER_DESIGN_MATCH.md` |
| Product-facing roles doc | `docs/product/admin_roles_and_permissions.md` |
| Achievements (badges, awards, lifecycle hooks) | `api/achievements/` — see also `docs/frontend/settings_and_achievements_handover.md` |
| Stored roles & permissions | `api/authorization/` (`registry.py`, `codenames.py`, `services/permission_service.py`, `services/role_admin_service.py`, `permissions.py`) |
| Workflow role checks | `api/users/workflow.py` |
| Engineering standards (house rules) | `docs/standards/ENGINEERING_STANDARDS.md` |
| Swagger/doc standard | `docs/standards/swagger_standard.md` |
