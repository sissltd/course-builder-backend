# End-to-End Testing Plan — Signup → Onboarding → Course Creation → Collaboration

A runnable, top-to-bottom manual test script covering everything built through
the collaboration feature. Each step is a `curl` command with the expected
response noted below it — run them in order, in a fresh shell, against a
locally running server (`python manage.py runserver 0.0.0.0:8000`) with a
migrated database.

This complements `docs/postman_collection.md` (the per-resource API
reference) rather than replacing it — where a step's contract hasn't changed
since that doc was written (Modules/Lessons/Assessments/Review Queue/Wallet),
this plan links to it instead of repeating full detail. Everything that
changed or is new this cycle (Onboarding's expertise field, Category
Requests, Topics, the new Course Information fields, and Collaboration) is
spelled out here in full.

## 0. Setup

```bash
export BASE_URL="http://localhost:8000"
```

Unless noted, every authenticated request needs:
```
Authorization: Bearer $ACCESS_TOKEN
Content-Type: application/json
```

Console email backend in local dev prints emails to the `runserver` terminal
— that's where you'll read verification links and the category-request
approval email.

Create an Admin test account once, up front (self-service signup always
creates a `COURSE_CREATOR`):
```bash
python manage.py shell -c "
from django.contrib.auth import get_user_model
from api.users.enums import UserRole
User = get_user_model()
User.objects.create_user(
    email='admin@example.com', password='AdminPass123!',
    first_name='Ada', last_name='Min',
    role=UserRole.ADMIN, is_active=True,
)
"
```
Log in as Admin normally (Step 1.4 below, same endpoint) and keep that token
in a separate `$ADMIN_TOKEN` variable — you'll need it for category-request
approval and topic creation.

---

## 1. Signup & Authentication

### 1.1 Signup (now requires `country`, optionally `terms_accepted`)
```bash
curl -s -X POST $BASE_URL/api/v1/auth/signup/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "creator@example.com",
    "password": "StrongPass123!",
    "first_name": "Grace",
    "last_name": "Hopper",
    "country": "US",
    "terms_accepted": true
  }'
```
**Expect 201.** `country` is required (ISO 3166-1 alpha-2, e.g. `"US"`,
`"NG"`) — omitting it now 400s, unlike before this cycle. Account is inactive
until verified; a verification link prints to the console.

### 1.2 Verify Email
Copy the `token` query param from the printed link, then:
```bash
curl -s -X POST $BASE_URL/api/v1/auth/verify-email/ \
  -H "Content-Type: application/json" \
  -d '{ "email": "creator@example.com", "token": "PASTE_TOKEN_HERE" }'
```
**Expect 200** with `{ "access": "...", "refresh": "...", "user": {...} }`.
```bash
export ACCESS_TOKEN="paste the access value here"
```

### 1.3 Get Current User
```bash
curl -s $BASE_URL/api/v1/users/me/ -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect 200.** Confirm `country: "US"`, `terms_accepted_at` is set (non-null,
since you passed `terms_accepted: true`), and `has_completed_onboarding: false`.

### 1.4 Admin Login (for later steps)
```bash
curl -s -X POST $BASE_URL/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{ "email": "admin@example.com", "password": "AdminPass123!" }'
```
```bash
export ADMIN_TOKEN="paste the access value here"
```

---

## 2. Onboarding Wizard

One resource, `PATCH` once per step — a dropped-off wizard resumes since only
the fields you send are touched. **Note:** `expertise_area` is a **fixed
enum** now (not a `category_id` reference like the old doc says).

### 2.1 Get Onboarding Status (lazily creates an empty profile)
```bash
curl -s $BASE_URL/api/v1/users/me/onboarding/ -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect 200**, `primary_expertise_area: ""`, `has_completed_onboarding: false`.

### 2.2 Step 1 — Area of expertise
```bash
curl -s -X PATCH $BASE_URL/api/v1/users/me/onboarding/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "expertise_area": "WEB_DEVELOPMENT" }'
```
Values: `WEB_DEVELOPMENT`, `DATA_SCIENCE_ANALYTICS`, `AI_MACHINE_LEARNING`,
`BUSINESS_MANAGEMENT`, `DIGITAL_MARKETING`, `LEADERSHIP_SOFT_SKILLS`,
`FINANCE_ACCOUNTING`, `OTHERS`. **`OTHERS` requires `other_expertise` in the
same call** (400 otherwise):
```bash
curl -s -X PATCH $BASE_URL/api/v1/users/me/onboarding/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "expertise_area": "OTHERS", "other_expertise": "Podcast production" }'
```

### 2.3 Step 2 — Level of proficiency
```bash
curl -s -X PATCH $BASE_URL/api/v1/users/me/onboarding/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "video_comfort_level": "VERY_COMFORTABLE" }'
```
Values: `NEEDS_GUIDANCE`, `SOMEWHAT_COMFORTABLE`, `VERY_COMFORTABLE`, `PREFERS_TEXT_AUDIO`.

### 2.4 Step 3 — Number of courses
```bash
curl -s -X PATCH $BASE_URL/api/v1/users/me/onboarding/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "monthly_course_capacity": "TWO_TO_THREE" }'
```
Values: `ONE`, `TWO_TO_THREE`, `FOUR_TO_FIVE`, `MORE_THAN_FIVE`.

### 2.5 Step 4 — Agreement (final step; now issues fresh tokens too)
```bash
curl -s -X PATCH $BASE_URL/api/v1/users/me/onboarding/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "agreement_accepted": true }'
```
**Expect 200** with `onboarding_completed_at` set, `has_completed_onboarding:
true`, **and `access`/`refresh` keys present alongside the profile** — this
is the "Signing you in..." step; confirm any *other* step (2.2-2.4) does
**not** include `access`/`refresh` in its response (they're only issued on
this final step).

### 2.6 Confirm via Get Current User
```bash
curl -s $BASE_URL/api/v1/users/me/ -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect** `has_completed_onboarding: true`.

---

## 3. Category Request Flow

### 3.1 List Categories (probably empty on a fresh DB)
```bash
curl -s $BASE_URL/api/v1/categories/ -H "Authorization: Bearer $ACCESS_TOKEN"
```

### 3.2 Submit a Category Request (as the Creator)
```bash
curl -s -X POST $BASE_URL/api/v1/category-requests/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "name": "Robotics" }'
```
**Expect 201**, `status: "PENDING"`. Note the returned `id` as `$REQUEST_ID`.

### 3.3 Creator Lists Their Own Requests
```bash
curl -s $BASE_URL/api/v1/category-requests/ -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect** only requests this creator submitted.

### 3.4 Admin Approves the Request
```bash
curl -s -X POST $BASE_URL/api/v1/category-requests/$REQUEST_ID/approve/ \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{ "creator_price": "40.00", "track_preference": "OPEN" }'
```
**Expect 200**, `status: "APPROVED"`, `resulting_category` populated. Check
the console log for the approval email (subject "Your category request has
been approved"). Note `resulting_category.id` as `$CATEGORY_ID`.

### 3.5 Negative Cases (optional but recommended)
- Approve twice → second call **400** ("cannot be approved from status 'APPROVED'").
- Submit a request as Creator, then try to `POST .../approve/` as the same
  Creator (not an admin) → **403**.
- Approve a request whose name already matches an existing Category → **400**
  ("A category with this name already exists").

---

## 4. Topics (nested under a Category, admin-managed)

### 4.1 Admin Creates a Topic
```bash
curl -s -X POST $BASE_URL/api/v1/topics/ \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d "{ \"category\": \"$CATEGORY_ID\", \"name\": \"Autonomous Drones\", \"creator_price\": \"25.00\", \"status\": \"ACTIVE\" }"
```
**Expect 201.** Note `id` as `$TOPIC_ID`.

### 4.2 Creator Lists Topics, Filtered by Category
```bash
curl -s "$BASE_URL/api/v1/topics/?category=$CATEGORY_ID" -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect** the Topic just created, scoped to that category.

### 4.3 Negative Case
Creating a second Topic with the **same name in the same category** → **400**
("unique" constraint). The same name in a *different* category is fine.

---

## 5. Course Creation (with the new Course Information fields)

### 5.1 Create a Draft Course
```bash
curl -s -X POST $BASE_URL/api/v1/courses/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d "{
    \"category\": \"$CATEGORY_ID\",
    \"topic\": \"$TOPIC_ID\",
    \"title\": \"Building Autonomous Drones\",
    \"description\": \"$(python3 -c 'print("word " * 150)')\",
    \"difficulty_level\": \"BEGINNER\",
    \"learning_objectives\": [\"Understand drone flight physics\", \"Assemble a basic flight controller\"],
    \"tags\": [\"Robotics\", \"Hardware\", \"Electronics\"],
    \"thumbnail_url\": \"https://cdn.example.com/thumbs/drones.jpg\",
    \"preview_video_url\": \"https://cdn.example.com/previews/drones.mp4\",
    \"duration_hours\": 2,
    \"duration_minutes\": 30,
    \"duration_seconds\": 0,
    \"terms_accepted\": true
  }"
```
**Expect 201.** Verify in the response: `topic.id` matches, `difficulty_level:
"BEGINNER"`, `learning_objectives`/`tags` echoed back as lists,
`planned_duration_seconds: 9000` (2h30m), `thumbnail_url` set,
`creator_price_snapshot: null` (not snapshotted until submit). Note `id` as
`$COURSE_ID`.

### 5.2 Negative Case — Mismatched Topic/Category
Create a second Category (or reuse an existing one) and try creating a course
with that category but the *first* category's topic → **400** ("topic does
not belong to the selected category").

### 5.3 Negative Case — Bad Tags/Objectives Shape
`"tags": ["ok", ""]` (empty string in the list) → **400** ("tags must be a
list of non-empty strings").

### 5.4 Partial Update — Confirm Duration Isn't Silently Zeroed
```bash
curl -s -X PATCH $BASE_URL/api/v1/courses/$COURSE_ID/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "title": "Building Autonomous Drones (Updated)" }'
```
**Expect 200**, and `planned_duration_seconds` **unchanged** (still `9000`) —
this confirms the partial-update fix: omitting all three duration fields
must not zero it out.

Then confirm supplying duration fields *does* recombine them:
```bash
curl -s -X PATCH $BASE_URL/api/v1/courses/$COURSE_ID/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "duration_hours": 3, "duration_minutes": 0, "duration_seconds": 0 }'
```
**Expect** `planned_duration_seconds: 10800`.

---

## 6. Modules → Lessons → Assessments → Submit → Review

These endpoints/contracts are unchanged — full detail (bodies, thresholds,
error shapes) is in `docs/postman_collection.md`'s **Modules**, **Lessons**,
**Assessments**, and **Review Queue** folders. For a minimally submittable
course you need (see that doc's "Course quality standards" table):

- 4–12 Modules, each with a Module-level Assessment
- 3–8 Lessons per Module, each with `script` (500–1500 words),
  `learning_objectives` (2–5 items); Lesson-level Assessments are optional
- A Course-level final Assessment with ≥15 questions
- `preview_video_url` set (done in step 5.1) and `terms_accepted` true (done at create)

Build that tree, then:
```bash
curl -s -X POST $BASE_URL/api/v1/courses/$COURSE_ID/submit/ -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect 200**, `status: "SUBMITTED"`, and **`creator_price_snapshot` equal to
the Topic's price ($25.00), not the Category's ($40.00)** — this is the
topic-price-wins-over-category-price rule from Piece 2; if you built the
course *without* a topic, it should snapshot the category price instead.

Then, as a Creator Reviewer or Admin, claim/approve/reject per the Review
Queue folder in `docs/postman_collection.md`.

---

## 7. Collaboration

Everything in this section is new. Create a **second** registered user first
(signup + verify, as in Section 1) to invite as a collaborator — call them
`bob@example.com`, and re-run Section 1.1-1.3 for them if you haven't already
(they don't need to complete onboarding).

### 7.1 Invite an Existing User
```bash
curl -s -X POST $BASE_URL/api/v1/courses/$COURSE_ID/collaborators/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "email": "bob@example.com", "role": "COLLABORATOR" }'
```
**Expect 201.** Response nests the invited user (`id`, `first_name`,
`last_name`, `email`, `country`, `sex`) plus `role` and `created_datetime`.
Note the collaborator row's `id` as `$COLLAB_ID`. Confirm an in-app
`Notification` was created for Bob (check Django admin, or
`Notification.objects.filter(receiver__email="bob@example.com")` in a shell).

### 7.2 Negative Cases
- Invite an email with no account (`nobody@example.com`) → **400** ("No
  account exists for this email...").
- Invite the course's own creator's email → **400** ("already the Author...").
- Invite the same person twice → **400** ("already a collaborator...").
- Log in as Bob (a plain `COLLABORATOR`) and try to invite a third person on
  this course → **403** ("Only the course creator or an Admin collaborator...").

### 7.3 List Collaborators (as creator, and as Bob)
```bash
curl -s $BASE_URL/api/v1/courses/$COURSE_ID/collaborators/ -H "Authorization: Bearer $ACCESS_TOKEN"
```
```bash
export BOB_TOKEN="Bob's access token from his own login"
curl -s $BASE_URL/api/v1/courses/$COURSE_ID/collaborators/ -H "Authorization: Bearer $BOB_TOKEN"
```
**Expect 200 for both** — anyone with course access (creator or any
collaborator) can see the collaborator list.

A third, unrelated user hitting the same URL should get **200 with an empty
`results` list**, not a 404 — the course's *existence* isn't confirmed or
denied via this endpoint (matches how nested Module/Lesson lists behave for
non-owners too).

### 7.4 Bob (plain Collaborator) Exercises Edit Access
```bash
curl -s $BASE_URL/api/v1/courses/$COURSE_ID/ -H "Authorization: Bearer $BOB_TOKEN"
```
**Expect 200** (collaborators can retrieve).
```bash
curl -s -X POST $BASE_URL/api/v1/courses/$COURSE_ID/modules/ \
  -H "Authorization: Bearer $BOB_TOKEN" -H "Content-Type: application/json" \
  -d '{ "title": "Module added by Bob", "order": 99 }'
```
**Expect 201** — this is the core "wire in edit permissions" behavior: Bob
can create a Module on a course he doesn't own, purely via the collaborator
row from 7.1. (Clean up this test module afterward if you want the course to
still pass the module-count threshold expected in Section 6.)

### 7.5 Bob Is Blocked From Submit/Delete
```bash
curl -s -X POST $BASE_URL/api/v1/courses/$COURSE_ID/submit/ -H "Authorization: Bearer $BOB_TOKEN"
curl -s -X DELETE $BASE_URL/api/v1/courses/$COURSE_ID/ -H "Authorization: Bearer $BOB_TOKEN"
```
**Expect 404 for both** — `submit`/`destroy` stay creator-only at the
queryset level (a collaborator's request doesn't even resolve the course for
these two actions, so it 404s rather than 403s).

### 7.6 Promote Bob to Admin, Then Bob Manages Collaborators
```bash
curl -s -X PATCH $BASE_URL/api/v1/courses/$COURSE_ID/collaborators/$COLLAB_ID/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{ "role": "ADMIN" }'
```
**Expect 200**, `role: "ADMIN"`. Now, as Bob, invite a third existing user:
```bash
curl -s -X POST $BASE_URL/api/v1/courses/$COURSE_ID/collaborators/ \
  -H "Authorization: Bearer $BOB_TOKEN" -H "Content-Type: application/json" \
  -d '{ "email": "carol@example.com" }'
```
**Expect 201** — an Admin-role collaborator can invite others; a plain
Collaborator (Section 7.2's last case) could not.

### 7.7 Remove a Collaborator
```bash
curl -s -X DELETE $BASE_URL/api/v1/courses/$COURSE_ID/collaborators/$COLLAB_ID/ \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```
**Expect 204.** Confirm Bob immediately loses access:
```bash
curl -s $BASE_URL/api/v1/courses/$COURSE_ID/ -H "Authorization: Bearer $BOB_TOKEN"
```
**Expect 404.**

---

## 8. Wallet (unchanged)

Full detail in `docs/postman_collection.md`'s **Wallet** folder — worth a
quick sanity check after Section 6's approval step: `GET /api/v1/wallet/`
should show the creator's balance credited by `creator_price_snapshot`.

---

## Sign-off checklist

- [ ] Signup rejects a missing `country`
- [ ] Onboarding's final step returns `access`/`refresh`; no other step does
- [ ] `OTHERS` expertise without `other_expertise` is rejected
- [ ] Category request → approve creates a real Category and emails the requester
- [ ] Topic price wins over Category price at submit time when a topic is set
- [ ] Course create/update round-trip every new field (`difficulty_level`, `tags`, `learning_objectives`, `thumbnail_url`, `planned_duration_seconds`)
- [ ] A partial course update that omits duration fields does not zero out `planned_duration_seconds`
- [ ] A Collaborator can edit (modules/lessons/assessments) but not submit/delete/publish/invite
- [ ] An Admin-role collaborator can invite/remove/change-role; a plain Collaborator cannot
- [ ] Removing a collaborator immediately revokes their access (404 on next request)
