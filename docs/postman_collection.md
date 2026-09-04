# SCCS Phase 1 — Postman Collection

API reference for the Phase 1 Creator Studio slice (Categories, Courses, Modules,
Lessons, Assessments, Review Queue, Wallet). Organized as Postman folders —
import-ready if you paste each request into Postman, or ask for the raw
`.postman_collection.json` export instead.

## Postman variables

Set these as a Postman **Environment** (Environments → New):

| Variable | Example value | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000` | No trailing slash |
| `access_token` | `eyJhbGciOi...` | See "Authentication" below |
| `category_id` | `<uuid>` | Set after creating/listing a category |
| `course_id` | `<uuid>` | Set after creating a course |
| `module_id` | `<uuid>` | Set after creating a module |
| `lesson_id` | `<uuid>` | Set after creating a lesson |

All requests below use `{{base_url}}/api/v1/...` and, unless noted, header:

```
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

## Authentication (read this first)

Signup/login/verify/refresh/logout are now real endpoints (see the **Authentication**
folder below) — get an `access_token` by running Signup → Verify Email (or Login,
for an already-verified user) and pasting the returned `access` value into the
`access_token` Postman variable. Verification and password reset are **link-based**:
the email contains a clickable URL (`{{FRONTEND_URL}}/verify-email?email=...&token=...`),
not a typed code — in Postman, copy the `token` query param out of the emailed link
(or the console-backend log in local dev) into the request body below.

For Creator Reviewer / Admin test accounts (self-service signup always creates a
`COURSE_CREATOR`), create one via Django admin or the shell:
```bash
python manage.py shell -c "
from django.contrib.auth import get_user_model
from api.users.enums import UserRole

User = get_user_model()
user = User.objects.create_user(
    email='reviewer@example.com', password='testpass123',
    first_name='Test', last_name='Reviewer',
    role=UserRole.CREATOR_REVIEWER, is_active=True,
)
"
```
Then log in as that user via `POST /api/v1/auth/login/` normally.

---

## Folder: Authentication

### Signup
`POST {{base_url}}/api/v1/auth/signup/`
Auth: AllowAny. Creates an inactive user (role always forced to `COURSE_CREATOR`) and emails a verification **link** (`FRONTEND_URL` + `/verify-email?email=...&token=...`, 60 min expiry by default). No tokens are issued here — the account can't authenticate until verified (unverified accounts are rejected project-wide by `rest_framework_simplejwt`'s `USER_AUTHENTICATION_RULE`, so an inert token would be useless anyway).

**Body**
```json
{ "email": "creator@example.com", "password": "StrongPass123!", "password_confirm": "StrongPass123!", "first_name": "Ada", "last_name": "Lovelace" }
```

**201 Created**
```json
{ "id": "...", "email": "creator@example.com", "first_name": "Ada", "last_name": "Lovelace", "role": "COURSE_CREATOR", "is_active": false, "status": "PENDING_VERIFICATION", "created_datetime": "2026-07-12T12:00:00Z", "updated_datetime": "2026-07-12T12:00:00Z", "has_completed_onboarding": false }
```

**400 Bad Request** (duplicate email)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "A user with this email already exists.", "field_name": "email" } ] }
```

### Reviewer Signup
`POST {{base_url}}/api/v1/auth/reviewer/signup/`
Auth: AllowAny. Identical to Signup above (same `SignupSerializer`, same
verification-link email), except the created account gets
`role: "CREATOR_REVIEWER"` instead of `COURSE_CREATOR`. A separate endpoint
by design, not a role field on `/auth/signup/` — this is **open
self-service**: anyone who signs up here becomes a Creator Reviewer, with
course-approval, wallet-crediting, and full Category/Topic pricing access.
No onboarding wizard applies to this role either (onboarding is entirely
opt-in for every role — nothing backend-side forces it).

**Body / response shapes**: identical to Signup, just `role: "CREATOR_REVIEWER"`.

### Verify Email
`POST {{base_url}}/api/v1/auth/verify-email/`
Auth: AllowAny. Consumes the link token, activates the account, and auto-issues tokens (login-on-verify) — no separate login call needed right after signup.

**Body**
```json
{ "email": "creator@example.com", "token": "iJ3nuXus6xZ2aUOVtETGuBp3U2nlIqK0YdAlyP1nBqE" }
```

**200 OK**
```json
{ "access": "eyJ...", "refresh": "eyJ...", "user": { "id": "...", "email": "creator@example.com", "...": "...", "is_active": true } }
```

**404 Not Found** (wrong/garbled token, wrong email, wrong purpose, or already used — all reported identically so a mismatch never reveals which part was wrong)
```json
{ "errors": [ { "type": "client_error", "code": "not_found", "message": "Invalid or expired verification link. Please request a new one.", "field_name": null } ] }
```

**400 Bad Request** (token exists but has expired)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "This link has expired. Please request a new one.", "field_name": "non_field_errors" } ] }
```

### Resend Verification
`POST {{base_url}}/api/v1/auth/resend-verification/`
Auth: AllowAny. Subject to a resend cooldown (`EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS`, default 60s).

**Body**
```json
{ "email": "creator@example.com", "purpose": "SIGNUP_VERIFICATION" }
```
`purpose` is `SIGNUP_VERIFICATION` or `PASSWORD_RESET`.

**200 OK**
```json
{ "detail": "A new verification link has been sent." }
```

**400 Bad Request** (cooldown not elapsed)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "Please wait before requesting another link.", "field_name": "non_field_errors" } ] }
```

### Login
`POST {{base_url}}/api/v1/auth/login/` (Creator) or
`POST {{base_url}}/api/v1/auth/reviewer/login/` (Reviewer — same view/behavior,
just a distinct URL for the reviewer frontend to call)
Auth: AllowAny. Standard email+password → JWT pair, for any active user
regardless of role. Deliberately **not** anti-enumeration: unknown email,
wrong password, and an unverified (`is_active=False`) account each return a
distinct field-scoped `validation_error` rather than one generic message.

**Body**
```json
{ "email": "creator@example.com", "password": "StrongPass123!" }
```

**200 OK**
```json
{ "access": "eyJ...", "refresh": "eyJ..." }
```

**400 Bad Request** (no account with this email)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "No account found with this email address.", "field_name": "email" } ] }
```

**400 Bad Request** (account exists, wrong password)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "Incorrect password.", "field_name": "password" } ] }
```

**400 Bad Request** (account exists, correct password, not yet verified)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "This account has not been verified yet. Please check your email for a verification link.", "field_name": "email" } ] }
```

**400 Bad Request** (missing `email` and/or `password` — standard required-field validation, unchanged)
```json
{ "errors": [ { "type": "validation_error", "code": "required", "message": "This field is required.", "field_name": "email" }, { "type": "validation_error", "code": "required", "message": "This field is required.", "field_name": "password" } ] }
```

### Refresh Token
`POST {{base_url}}/api/v1/auth/token/refresh/`
Auth: AllowAny. Stock `rest_framework_simplejwt` `TokenRefreshView` — unwrapped. Rotates the refresh token and blacklists the old one (`ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION`, both already configured).

**Body**
```json
{ "refresh": "eyJ..." }
```

**200 OK**
```json
{ "access": "eyJ...", "refresh": "eyJ..." }
```

**401 Unauthorized** (expired or blacklisted token)
```json
{ "errors": [ { "type": "client_error", "code": "token_not_valid", "message": "Token is blacklisted", "field_name": "detail" } ] }
```

### Logout
`POST {{base_url}}/api/v1/auth/logout/`
Auth: **IsAuthenticated** (access token in header, *and* the refresh token to blacklist in the body) — deliberately not AllowAny-with-token-alone, so a stolen refresh token alone can't be used as a blacklist oracle.

**Body**
```json
{ "refresh": "eyJ..." }
```

**200 OK**
```json
{ "detail": "Logged out." }
```

### Forgot Password
`POST {{base_url}}/api/v1/auth/forgot-password/`
Auth: AllowAny. Always returns 200 regardless of whether the email exists (anti-enumeration) — only a real match actually gets an email, containing a `FRONTEND_URL` + `/reset-password?email=...&token=...` link.

**Body**
```json
{ "email": "creator@example.com" }
```

**200 OK**
```json
{ "detail": "If an account exists for this email, a reset link has been sent." }
```

### Reset Password
`POST {{base_url}}/api/v1/auth/reset-password/`
Auth: AllowAny. Consumes the `PASSWORD_RESET`-purpose link token from Forgot Password (or Resend Verification with that purpose).

**Body**
```json
{ "email": "creator@example.com", "token": "8pQrT2v...", "new_password": "BrandNewPass456!" }
```

**200 OK**
```json
{ "detail": "Password reset successfully." }
```

**400 Bad Request** (expired token, or `new_password` fails Django's `AUTH_PASSWORD_VALIDATORS`) / **404 Not Found** (wrong/used token, matching the Verify Email semantics above)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "This link has expired. Please request a new one.", "field_name": "non_field_errors" } ] }
```

### Get Current User
`GET {{base_url}}/api/v1/users/me/`
Auth: IsAuthenticated.

**200 OK**
```json
{ "id": "...", "email": "creator@example.com", "first_name": "Ada", "last_name": "Lovelace", "role": "COURSE_CREATOR", "is_active": true, "created_datetime": "2026-07-12T12:00:00Z", "has_completed_onboarding": false }
```
`has_completed_onboarding` is `true` once the onboarding wizard's final ("Agreement") step has been submitted — use it right after Signup/Verify Email/Login to decide whether to route the client to the onboarding wizard or straight to the dashboard.

---

## Folder: Onboarding

The post-signup wizard (Area of Expertise → Level of Proficiency → Number of Courses →
Agreement Policy) is one resource with partial updates — call `PATCH` once per wizard
step with just that step's field(s); a dropped-off wizard resumes automatically since
only the fields you send are touched.

### Get Onboarding Status
`GET {{base_url}}/api/v1/users/me/onboarding/`
Auth: IsAuthenticated. Lazily creates an empty profile on first call.

**200 OK**
```json
{
  "id": "...",
  "primary_expertise_category": null,
  "primary_expertise_other": "",
  "video_comfort_level": "",
  "monthly_course_capacity": "",
  "agreement_accepted_at": null,
  "onboarding_completed_at": null,
  "has_completed_onboarding": false
}
```

### Update Onboarding (one call per wizard step)
`PATCH {{base_url}}/api/v1/users/me/onboarding/`
Auth: IsAuthenticated. All fields optional — at least one required per call.

**Step 1 — Area of expertise** (single-select; `category_id` references an existing active `Category`, or use `other_expertise` alone for "Others (Specify)")
```json
{ "category_id": "{{category_id}}" }
```
or
```json
{ "other_expertise": "Podcast production" }
```

**Step 2 — Level of proficiency** (really: video-content comfort)
```json
{ "video_comfort_level": "VERY_COMFORTABLE" }
```
Values: `NEEDS_GUIDANCE`, `SOMEWHAT_COMFORTABLE`, `VERY_COMFORTABLE`, `PREFERS_TEXT_AUDIO`.

**Step 3 — Number of courses** (monthly capacity bucket)
```json
{ "monthly_course_capacity": "TWO_TO_THREE" }
```
Values: `ONE`, `TWO_TO_THREE`, `FOUR_TO_FIVE`, `MORE_THAN_FIVE`.

**Step 4 — Agreement policy** (final step — completes onboarding)
```json
{ "agreement_accepted": true }
```

**200 OK** (after the final step)
```json
{
  "id": "...",
  "primary_expertise_category": { "id": "...", "name": "Web Development" },
  "primary_expertise_other": "",
  "video_comfort_level": "VERY_COMFORTABLE",
  "monthly_course_capacity": "TWO_TO_THREE",
  "agreement_accepted_at": "2026-07-12T19:23:12Z",
  "onboarding_completed_at": "2026-07-12T19:23:12Z",
  "has_completed_onboarding": true
}
```

**400 Bad Request** (empty body, or `category_id` references an inactive/nonexistent category)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "At least one onboarding field must be provided.", "field_name": "non_field_errors" } ] }
```

---

## Folder: Categories

### List Categories
`GET {{base_url}}/api/v1/categories/`
Auth: any authenticated user (Creator/Reviewer/Admin)
Query params (optional): `track_preference` (`CREATOR_PREFERRED`|`AI_PREFERRED`|`OPEN`), `status` (`ACTIVE`|`INACTIVE`), `ordering` (`name`, `creator_price`, `created_datetime`, prefix `-` to reverse), `size` (page size)

**200 OK**
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
    "results": [
      {
        "id": "5f2c1e2a-1234-4a11-9f3a-000000000001",
        "name": "Web Development",
        "description": "Frontend, backend, and full-stack web courses.",
        "creator_price": "75.00",
        "track_preference": "OPEN",
        "status": "ACTIVE",
        "created_datetime": "2026-07-11T10:00:00Z",
        "updated_datetime": "2026-07-11T10:00:00Z"
      }
    ]
  }
}
```

### Retrieve Category
`GET {{base_url}}/api/v1/categories/{{category_id}}/`
Auth: any authenticated user

**200 OK** — same object shape as one item in the list above (not paginated).

### Create Category — Admin or Creator Reviewer
`POST {{base_url}}/api/v1/categories/`

**Body**
```json
{
  "name": "AI & Machine Learning",
  "description": "Beginner to advanced AI/ML courses.",
  "creator_price": "100.00",
  "track_preference": "AI_PREFERRED",
  "status": "ACTIVE"
}
```

**201 Created**
```json
{
  "id": "5f2c1e2a-1234-4a11-9f3a-000000000002",
  "name": "AI & Machine Learning",
  "description": "Beginner to advanced AI/ML courses.",
  "creator_price": "100.00",
  "track_preference": "AI_PREFERRED",
  "status": "ACTIVE"
}
```

**403 Forbidden** (non-admin token)
```json
{
  "errors": [
    {
      "type": "client_error",
      "code": "permission_denied",
      "message": "You do not have permission to perform this action.",
      "field_name": null
    }
  ]
}
```

**400 Bad Request** (duplicate `name`)
```json
{
  "errors": [
    {
      "type": "validation_error",
      "code": "unique",
      "message": "category with this name already exists.",
      "field_name": "name"
    }
  ]
}
```

### Update Category — Admin or Creator Reviewer
`PATCH {{base_url}}/api/v1/categories/{{category_id}}/`

**Body**
```json
{ "creator_price": "120.00" }
```

**200 OK** — updated category object. *(Existing in-progress/submitted courses keep their `creator_price_snapshot`; only courses submitted after this change pick up the new price.)*

### Delete Category — Admin or Creator Reviewer
`DELETE {{base_url}}/api/v1/categories/{{category_id}}/`

**204 No Content** (empty body)

---

## Folder: Courses (Creator Track)

All endpoints in this folder are scoped to the authenticated Creator's own
courses — another creator's course 404s rather than 403s (existence isn't leaked).

### Create Draft Course
`POST {{base_url}}/api/v1/courses/`
Auth: Course Creator role

**Body**
```json
{
  "category": "{{category_id}}",
  "title": "Python for Data Science",
  "description": "A 150+ word description covering target audience, prerequisites, and outcomes... (100-500 words required at submit time)",
  "learning_objectives": ["Objective 1", "Objective 2", "Objective 3", "Objective 4", "Objective 5"],
  "version": "{{course_version_id}}",
  "preview_video_url": "https://cdn.example.com/previews/python-ds.mp4",
  "terms_accepted": true
}
```

**201 Created**
```json
{
  "id": "8a1e2b3c-4444-4a11-9f3a-000000000010",
  "title": "Python for Data Science",
  "description": "A 150+ word description covering target audience, prerequisites, and outcomes...",
  "category": { "id": "5f2c1e2a-1234-4a11-9f3a-000000000001", "name": "Web Development" },
  "status": "DRAFT",
  "creator_price_snapshot": null,
  "preview_video_url": "https://cdn.example.com/previews/python-ds.mp4",
  "terms_accepted_at": "2026-07-11T10:05:00Z",
  "submitted_at": null,
  "approved_at": null,
  "published_at": null,
  "rejected_at": null,
  "modules": [],
  "final_assessment": null,
  "duration_estimate_minutes": 0,
  "created_datetime": "2026-07-11T10:05:00Z",
  "updated_datetime": "2026-07-11T10:05:00Z"
}
```

**400 Bad Request** (`terms_accepted: false`)
```json
{
  "errors": [
    {
      "type": "validation_error",
      "code": "invalid",
      "message": "You must accept the category Terms and Conditions to create a course.",
      "field_name": "non_field_errors"
    }
  ]
}
```

### List My Courses
`GET {{base_url}}/api/v1/courses/`
Auth: Course Creator role — returns only courses you created.

**200 OK**
```json
{
  "status": true,
  "message": "Successfully retrieved data",
  "data": {
    "paginator": { "count": 1, "page": 1, "page_size": 10, "total_pages": 1, "next": null, "next_page_number": null, "previous": null, "previous_page_number": null },
    "results": [
      {
        "id": "8a1e2b3c-4444-4a11-9f3a-000000000010",
        "title": "Python for Data Science",
        "category": { "id": "5f2c1e2a-1234-4a11-9f3a-000000000001", "name": "Web Development" },
        "status": "DRAFT",
        "creator_price_snapshot": null,
        "submitted_at": null,
        "created_datetime": "2026-07-11T10:05:00Z"
      }
    ]
  }
}
```

### Retrieve Course
`GET {{base_url}}/api/v1/courses/{{course_id}}/`
Auth: owning Course Creator only (else 404)

**200 OK** — full `CourseDetailSerializer` shape (same as the Create response above), with `modules[]` populated once you've added them (see Modules/Lessons folders), each module including its nested `lessons[]` and `assessment`.

**404 Not Found** (not your course)
```json
{
  "errors": [
    { "type": "client_error", "code": "not_found", "message": "No Course matches the given query.", "field_name": null }
  ]
}
```

### Update Draft Course
`PATCH {{base_url}}/api/v1/courses/{{course_id}}/`
Auth: owning Course Creator, **Draft status only**

**Body**
```json
{ "title": "Python for Data Science — Updated", "version": "{{course_version_id}}" }
```

**200 OK** — updated `CourseDetailSerializer` object.

**400 Bad Request** (course no longer Draft)
```json
{
  "errors": [
    { "type": "validation_error", "code": "invalid", "message": "Only Draft courses can be edited.", "field_name": "non_field_errors" }
  ]
}
```

### List Course Versions
`GET {{base_url}}/api/v1/course-versions/`

Returns active values for the builder's Versioning step. Save the selected
`id` through course create or update.

**200 OK** — standard paginated envelope; each item in `data.results` is:

```json
{ "id": "2f9a1e4b-7c8d-4a6e-9f0c-2d3e4f5a6b7c", "label": "1.0" }
```

### Delete Draft Course
`DELETE {{base_url}}/api/v1/courses/{{course_id}}/`
Auth: owning Course Creator, **Draft status only**

**204 No Content**

### Submit Course
`POST {{base_url}}/api/v1/courses/{{course_id}}/submit/`
Auth: owning Course Creator. Transitions `Draft → Submitted`. Runs the PRD §6.1-6.3
structural-standards check first (module/lesson counts, word counts, quiz counts,
preview video, terms acceptance, final assessment) — see [Course Quality Standards](#course-quality-standards-checked-at-submit) below.

**200 OK**
```json
{
  "id": "8a1e2b3c-4444-4a11-9f3a-000000000010",
  "title": "Python for Data Science",
  "status": "SUBMITTED",
  "creator_price_snapshot": "75.00",
  "submitted_at": "2026-07-11T11:00:00Z",
  "...": "...full CourseDetailSerializer fields..."
}
```

**400 Bad Request** (fails structural standards — one entry per failed rule)
```json
{
  "errors": [
    { "type": "validation_error", "code": "invalid", "message": "Course must have between 4 and 12 modules (has 1).", "field_name": "structural_standards" },
    { "type": "validation_error", "code": "invalid", "message": "Course must have a preview video before submission (BR-015).", "field_name": "structural_standards" },
    { "type": "validation_error", "code": "invalid", "message": "Course must have a final assessment with at least 15 questions (has 0).", "field_name": "structural_standards" }
  ]
}
```

### Publish Course — Admin only
`POST {{base_url}}/api/v1/courses/{{course_id}}/publish/`
Auth: Admin role (not owner-restricted — an Admin publishes any Approved course). Requires status `Approved`.

**200 OK** — `CourseDetailSerializer` with `status: "PUBLISHED"`, `published_at` set.

**400 Bad Request** (wrong source status)
```json
{
  "errors": [
    { "type": "validation_error", "code": "invalid", "message": "Course cannot be published from status 'DRAFT'.", "field_name": "non_field_errors" }
  ]
}
```

---

## Folder: Modules

Nested under a course. Sub-resource CRUD, **Draft courses only**.

### List Modules
`GET {{base_url}}/api/v1/courses/{{course_id}}/modules/`

**200 OK** — same paginated envelope as `/courses/`; each result is the full
read shape (`id`, `title`, `order`, nested `lessons[]`, `assessment`).

### Create Module
`POST {{base_url}}/api/v1/courses/{{course_id}}/modules/`

**Body**
```json
{
  "title": "Getting Started with Python",
  "order": 1,
  "description": "Set up Python and run a first program.",
  "learning_objectives": [
    "Install Python and a code editor",
    "Run a Python program from the command line"
  ]
}
```

**201 Created**
```json
{
  "id": "m1...",
  "title": "Getting Started with Python",
  "order": 1,
  "description": "Set up Python and run a first program.",
  "learning_objectives": [
    "Install Python and a code editor",
    "Run a Python program from the command line"
  ]
}
```
*(The create/update response uses the write serializer's minimal shape —
`id`, `title`, `order`, `description`, `learning_objectives` — not the nested
`lessons`/`assessment` read shape.
`GET`/`retrieve` a module to see its full tree.)*

### Retrieve / Update / Delete Module
`GET|PUT|PATCH|DELETE {{base_url}}/api/v1/courses/{{course_id}}/modules/{{module_id}}/`

**PUT/PATCH body**
```json
{
  "title": "Getting Started with Python (Revised)",
  "order": 1,
  "description": "Updated module description.",
  "learning_objectives": ["Build and run a basic Python program"]
}
```

**400 Bad Request** (course not Draft)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "Modules can only be edited while the course is Draft.", "field_name": "non_field_errors" } ] }
```

---

## Folder: Lessons

Nested under a course's module. Same Draft-only rule as Modules.

### List Lessons
`GET {{base_url}}/api/v1/courses/{{course_id}}/modules/{{module_id}}/lessons/`

**200 OK** — paginated envelope; each result is the full read shape (`id`,
`title`, `order`, `lesson_type`, deprecated `content_type`, `script`, `video_url`, `embedded_link`,
`video_script_file`, `learning_objectives`, `duration_minutes`, `assessment`,
`content_blocks`, `images`, `requirements`).

### Create Lesson
`POST {{base_url}}/api/v1/courses/{{course_id}}/modules/{{module_id}}/lessons/`

**Body**
```json
{
  "title": "Installing Python and Your First Script",
  "order": 1,
  "lesson_type": "VIDEO",
  "script": "Welcome to this lesson... (500-1500 words required at submit time)",
  "video_url": "https://cdn.example.com/lessons/installing-python.mp4",
  "embedded_link": "",
  "video_script_file": "uploads/lessons/installing-python.srt",
  "learning_objectives": [
    "Install Python 3, a code editor, and the required command-line tools",
    "Run a first script from the command line"
  ],
  "duration_minutes": 20,
  "requirements": [
    {
      "text": "Basic computer literacy and access to Python 3.",
      "order": 1
    }
  ]
}
```

**201 Created**
```json
{
  "id": "l1...",
  "title": "Installing Python and Your First Script",
  "order": 1,
  "lesson_type": "VIDEO",
  "content_type": "VIDEO",
  "script": "Welcome to this lesson...",
  "video_url": "https://cdn.example.com/lessons/installing-python.mp4",
  "embedded_link": "",
  "video_script_file": "uploads/lessons/installing-python.srt",
  "learning_objectives": ["Install Python 3, a code editor, and the required command-line tools", "Run a first script from the command line"],
  "duration_minutes": 20,
  "requirements": [
    {
      "id": "r1...",
      "text": "Basic computer literacy and access to Python 3.",
      "order": 1
    }
  ]
}
```
*(Write-serializer shape again — `assessment` isn't included; `GET`/retrieve
the lesson to see it once you've added one.)*

`lesson_type` matches the three **Add lesson** choices in the Figma builder:

- `VIDEO` — requires either `video_url` or `embedded_link`.
- `QUIZ` — create the lesson first, then attach its questions through the
  lesson assessment endpoint using the returned lesson `id`.
- `TEXT` — written lesson content; this is also the backwards-compatible
  default when an older client omits the type.

`content_type` is a deprecated read/write alias retained temporarily for existing
clients. New integrations should send and read `lesson_type`. Responses include
both fields during the transition, and requests containing different values for
the two aliases return 400.

`learning_objectives` is a JSON array: each array item is one complete
objective. A comma inside an item remains part of that objective; only a new
array item creates another objective. `requirements` is the ordered Lesson
Requirement content shown in Figma. Supplying `requirements` on PUT/PATCH
replaces the current list, sending `[]` clears it, and omitting the field leaves
the current requirements unchanged. The dedicated `/requirements/` endpoints
remain available for editing one requirement at a time.

### Retrieve / Update / Delete Lesson
`GET|PUT|PATCH|DELETE {{base_url}}/api/v1/courses/{{course_id}}/modules/{{module_id}}/lessons/{{lesson_id}}/`

Same request/response shape as create; same "course must be Draft" 400 as Modules.

---

## Folder: Assessments

Each of Lesson / Module / Course has **at most one** assessment (1:1). `GET` 404s
if none exists yet; `PUT` upserts (creates if absent, otherwise updates) —
**Draft courses only**.

Question shape (used identically at all three levels):
```json
{ "question": "What keyword defines a function in Python?", "options": ["func", "def", "function", "lambda"], "correct_index": 1 }
```

### Lesson Assessment
`GET|PUT {{base_url}}/api/v1/courses/{{course_id}}/modules/{{module_id}}/lessons/{{lesson_id}}/assessment/`

**PUT body** (3-5 questions required at submit time)
```json
{
  "title": "Lesson 1 Quiz",
  "questions": [
    { "question": "What keyword defines a function in Python?", "options": ["func", "def", "function", "lambda"], "correct_index": 1 },
    { "question": "Which symbol starts a comment?", "options": ["//", "#", "--", "<!--"], "correct_index": 1 },
    { "question": "What does `len([1,2,3])` return?", "options": ["2", "3", "4", "Error"], "correct_index": 1 }
  ]
}
```

**200 OK**
```json
{ "id": "a1...", "level": "LESSON", "title": "Lesson 1 Quiz", "questions": [ /* as submitted */ ] }
```

**404 Not Found** (no assessment yet)
```json
{ "errors": [ { "type": "client_error", "code": "not_found", "message": "Assessment not found.", "field_name": null } ] }
```

**400 Bad Request** (bad question shape — e.g. only 1 option)
```json
{
  "errors": [
    { "type": "validation_error", "code": "invalid", "message": "Question 0 must have at least 2 'options'.", "field_name": "questions" }
  ]
}
```

### Module Assessment (1 comprehensive quiz per module)
`GET|PUT {{base_url}}/api/v1/courses/{{course_id}}/modules/{{module_id}}/assessment/`
Same request/response shape as Lesson Assessment, with `"level": "MODULE"`.

### Course Final Assessment (minimum 15 questions)
`GET|PUT {{base_url}}/api/v1/courses/{{course_id}}/final-assessment/`
Same shape, `"level": "COURSE"`. `questions` must have **≥15** entries before the course can be submitted.

---

## Folder: Review Queue (Creator Reviewer / Admin)

`list` covers every stage a reviewer needs: `Submitted`/`In Review` (the
actual pending queue), plus `Approved` and `Published` — narrow to one via
`?status=`, or omit it to see all four together. The detail actions
(`retrieve`/`claim`/`approve`/`reject`) look up any course by id — acting on
a course in the wrong status returns **400** (wrong state), not 404.

### List Queue
`GET {{base_url}}/api/v1/review-queue/`
Auth: Creator Reviewer or Admin role
Query params: `status` (`SUBMITTED`|`IN_REVIEW`|`APPROVED`|`PUBLISHED`), `category`, `ordering=submitted_at` (default, oldest first)

**200 OK** — same paginated envelope as `/courses/`, items are `CourseListSerializer` objects, oldest `submitted_at` first. With no `status` filter, all four statuses are returned together.

### Retrieve Course in Queue
`GET {{base_url}}/api/v1/review-queue/{{course_id}}/`
Auth: Creator Reviewer or Admin — full `CourseDetailSerializer`.

### Claim for Review
`POST {{base_url}}/api/v1/review-queue/{{course_id}}/claim/`
`Submitted → In Review`. Idempotent if already claimed.

**200 OK** — `CourseDetailSerializer` with `"status": "IN_REVIEW"`.

### Approve
`POST {{base_url}}/api/v1/review-queue/{{course_id}}/approve/`

**Body** (feedback optional)
```json
{ "feedback": { "summary": "Great course, approved as-is." } }
```

**200 OK**
```json
{
  "id": "ra1...",
  "course": "8a1e2b3c-4444-4a11-9f3a-000000000010",
  "reviewer": { "id": "u1...", "email": "reviewer@example.com" },
  "action": "APPROVE",
  "feedback": { "summary": "Great course, approved as-is." },
  "created_datetime": "2026-07-11T12:00:00Z"
}
```
Side effects: `Course.status → APPROVED`, creator's `Wallet.balance` credited by
`creator_price_snapshot`, an in-app `Notification` sent to the creator.

**400 Bad Request** (already approved / wrong status — prevents double-approval)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "Course cannot be approved from status 'APPROVED'.", "field_name": "non_field_errors" } ] }
```

**403 Forbidden** (course creator trying to review their own course, or a plain Course Creator with no reviewer/admin role)
```json
{ "errors": [ { "type": "client_error", "code": "permission_denied", "message": "You do not have permission to perform this action.", "field_name": null } ] }
```

### Reject
`POST {{base_url}}/api/v1/review-queue/{{course_id}}/reject/`
`feedback.summary` is **required**.

**Body**
```json
{
  "feedback": {
    "summary": "Module 3's quiz questions don't align with the stated learning objectives.",
    "items": [
      { "module_id": "m3...", "lesson_id": null, "comment": "Add a question covering the 'debugging' objective." }
    ]
  }
}
```

**200 OK** — `ReviewActionSerializer` object, `"action": "REJECT"`. Side effect:
`Course.status → DRAFT` (immediately reopened for revision, not held at a
separate "Rejected" state), `rejected_at` set, creator notified with the feedback.

**400 Bad Request** (missing summary)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "feedback.summary is required.", "field_name": "feedback" } ] }
```

---

## Folder: Wallet (Creator Track)

### Get Wallet
`GET {{base_url}}/api/v1/wallet/`
Auth: Course Creator role. Auto-provisions a zero-balance wallet on first access.

**200 OK**
```json
{ "id": "w1...", "balance": "75.00", "currency": "USD", "updated_datetime": "2026-07-11T12:00:00Z" }
```

### List Transactions
`GET {{base_url}}/api/v1/transactions/`
Query params: `type` (`CREDIT`|`DEBIT`), `status` (`PENDING`|`COMPLETED`|`FAILED`), `ordering` (`created_datetime`, `amount`)

**200 OK**
```json
{
  "status": true,
  "message": "Successfully retrieved data",
  "data": {
    "paginator": { "count": 1, "page": 1, "page_size": 10, "total_pages": 1, "next": null, "next_page_number": null, "previous": null, "previous_page_number": null },
    "results": [
      {
        "id": "t1...",
        "course": { "id": "8a1e2b3c-4444-4a11-9f3a-000000000010", "title": "Python for Data Science" },
        "amount": "75.00",
        "type": "CREDIT",
        "status": "COMPLETED",
        "description": "Course 'Python for Data Science' approved",
        "created_datetime": "2026-07-11T12:00:00Z"
      }
    ]
  }
}
```

### Create Withdrawal Request
`POST {{base_url}}/api/v1/withdrawals/`

**Body**
```json
{ "amount": "60.00" }
```

**201 Created**
```json
{
  "id": "t2...",
  "course": null,
  "amount": "60.00",
  "type": "DEBIT",
  "status": "PENDING",
  "description": "Withdrawal request",
  "created_datetime": "2026-07-11T12:05:00Z"
}
```
*(Stays `PENDING` — payment-gateway payout/settlement is not part of Phase 1.)*

**400 Bad Request** (below minimum threshold, default $50 — configurable via `MINIMUM_WITHDRAWAL_THRESHOLD`)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "Minimum withdrawal amount is 50.00.", "field_name": "non_field_errors" } ] }
```

**400 Bad Request** (amount exceeds balance)
```json
{ "errors": [ { "type": "validation_error", "code": "invalid", "message": "Withdrawal amount exceeds available balance.", "field_name": "non_field_errors" } ] }
```

---

## Reference

### Course lifecycle
`DRAFT → SUBMITTED → IN_REVIEW → APPROVED → PUBLISHED`, with `REJECTED` never
persisted as a status (a rejection immediately reopens the course as `DRAFT`).

### Course quality standards (checked at submit)
| Rule | Threshold | Setting |
|---|---|---|
| Modules per course | 4–12 | `course_module_count_min/max` |
| Lessons per module | 3–8 | `course_lessons_per_module_min/max` |
| Learning objectives per course | 5 | `course_learning_objectives_min/max` |
| Learning objectives per lesson | 2–5 | `lesson_learning_objectives_min/max` |
| Lesson script length | 500–1500 words | `lesson_script_word_min/max` |
| Quiz questions per lesson | 3–5 | `lesson_quiz_questions_min/max` |
| Module-level assessment | required (1 per module) | — |
| Course description length | 100–500 words | `course_description_word_min/max` |
| Total course duration | 2–8 hours | `course_duration_min/max_minutes` |
| Preview video | required (BR-015) | — |
| Course version | required | `GET /course-versions/` |
| Terms accepted | required (BR-005) | — |
| Final assessment | required, ≥15 questions | `course_final_assessment_min_questions` |

### Roles
| Role | Value | Can do |
|---|---|---|
| Course Creator | `COURSE_CREATOR` | Categories (read), own Courses (full CRUD + submit), own Wallet |
| Creator Reviewer | `CREATOR_REVIEWER` | Review queue: list/retrieve/claim/approve/reject |
| Admin | `ADMIN` | Everything above + Category CRUD + Publish. `is_superuser` also always passes role checks. |

### Error response envelope (all non-2xx responses)
```json
{ "errors": [ { "type": "validation_error|client_error|server_error", "code": "...", "message": "...", "field_name": "field_name_or_null" } ] }
```

### List response envelope (all paginated `list` endpoints)
```json
{ "status": true, "message": "Successfully retrieved data", "data": { "paginator": { "...": "..." }, "results": [ "..." ] } }
```
