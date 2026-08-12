# Feexeet Swagger Documentation Standard

**Audience:** every backend engineer adding or editing an endpoint.
**Goal:** a frontend developer opens `/api/schema/swagger/`, finds any endpoint, and knows **what to send, what they'll get back, who can call it, and what every error means** — without ever asking a backend engineer.

This is the **non-negotiable contract** for endpoint documentation.

---

## 1. `summary` — imperative short title

- Maximum 60 characters.
- Starts with a verb in the imperative mood.
- No trailing period.

| Good                               | Bad                             |
| ---------------------------------- | ------------------------------- |
| `"Complete vendor onboarding"`     | `"Vendor onboarding endpoint."` |
| `"List active product categories"` | `"Get categories"`              |
| `"Approve a brand suggestion"`     | `"Brand approval"`              |

---

## 2. `description` — rich, multi-section explanation

The description is the **most important field**. Frontend devs will read it before anything else. Treat it like a mini-README for the endpoint.

Every description must have **all five** sections below, in this order, even if a section is one line.

### Required structure

```
<Opening paragraph — what this endpoint does, in plain English, in 2–4 sentences. Explain the business purpose, not the implementation.>

<Optional second paragraph — when to call this endpoint in the user journey. e.g. "Called immediately after the OTP verify step, before the user can access any vendor screens.">

**Auth:** <required role + any extra gate. e.g. "Vendor (KYC-verified)" or "Admin" or "Public — no auth required". Always include, even when public.>

**Prerequisites:** <what must already be true on the server side. e.g. "User must have a valid Bearer token from /auth/otp/verify". Write "None" if there are no prerequisites.>

**Important:** <gotchas — idempotency, side-effects, irreversible actions, rate limits, async behavior, anything that could trip up an integrator. Write "None" if there really are none.>
```

### Why this matters

- The **opening paragraph** sells the endpoint — what problem it solves.
- The **journey paragraph** answers "where does this fit in the flow?"
- **Auth** stops the frontend from making 403'd calls they could have predicted.
- **Prerequisites** stops them from calling endpoints out of order.
- **Important** is where you put the trap — the thing that, if missed, causes a Slack message at 11pm.

### Example

```
description=(
    "Completes the vendor onboarding flow by collecting business "
    "details, identity documents, and password. Creates the base "
    "UserProfile and VendorProfile in a single atomic transaction "
    "and assigns the Vendor role automatically.\n\n"
    "This is step 3 of vendor signup: invitation → OTP verify → "
    "**onboarding** → KYC. After this call succeeds, the vendor can "
    "log in with email + password but cannot trade until KYC clears.\n\n"
    "**Auth:** Bearer token from `/auth/otp/verify` (the temporary "
    "post-OTP token, not a long-lived session token).\n\n"
    "**Prerequisites:** The user must have verified their email via "
    "OTP. The same email used for OTP must match the token's claim.\n\n"
    "**Important:** This endpoint is idempotent on the user — a second "
    "call with the same token returns 409 Conflict. The password set "
    "here becomes the vendor's permanent login credential; subsequent "
    "logins use email + password (no OTP)."
),
```

---

## 3. `tags` — exactly one tag, format `Audience — Resource`

- Use the em dash `—` (U+2014) with a space on each side.
- Audience comes first so Swagger groups by who calls the endpoint.
- One tag per endpoint, no exceptions.

### Approved audiences

| Audience   | When to use                                                        |
| ---------- | ------------------------------------------------------------------- |
| `Public`   | No auth required — anyone can call (e.g. bank lookup, health checks) |
| `Auth`     | Auth lifecycle — signup, login, OTP/email verification, password, MFA, sessions, staff invitation |
| `Creator`  | Called by Course Creators and Writers (course authoring, wallet, collaborators) |
| `Reviewer` | Called by Creator Reviewers / Verifiers / Approvers / AI / QA (review queues) |
| `Admin`    | Called by Admins and Super Admins (platform management, KYC review, staff) |
| `System`   | Webhooks, internal callbacks (e.g. Paystack)                        |

### Approved tag examples

```
"Auth — Session"
"Auth — Signup & Verification"
"Auth — Password"
"Auth — MFA"
"Admin — Categories"
"Admin — Users"
"Admin — Wallets"
"Admin — Staff"
"Creator — Courses"
"Creator — Modules"
"Creator — Lessons"
"Creator — Assessments"
"Creator — Topics"
"Creator — Topic Reservations"
"Creator — Collaborators"
"Creator — Wallet"
"Reviewer — Review Queue"
"Reviewer — Availability"
"Reviewer — Queue Preferences"
"Reviewer — Topic Reservations"
"System — Webhooks"
```

### Not allowed

- Other separators: `"Creator • Courses"`, `"Creator-Courses"`, `"Courses--Creator"`.
- Multiple tags on one endpoint.
- Tags without audience: `"Courses"`, `"Wallet"`, `"Quiz"`.
- Resource-first tags: `"Courses — Topics"`, `"Users — KYC"`.

---

## 4. `request` — serializer + at least one `OpenApiExample`

Every endpoint that accepts a request body must declare both.

```python
request=VendorOnboardingSerializer,
examples=[
    OpenApiExample(
        name="Sample Request",
        request_only=True,
        value={
            "first_name": "Chinedu",
            "last_name": "Okafor",
            ...all fields populated with realistic values...
        },
    ),
],
```

- The example must be **realistic** — use plausible names, addresses, IDs (not `"string"` or `"test"`).
- If multiple valid shapes exist (e.g. individual vs B2B homeowner), include one example per shape.
- For GET endpoints with query params, document them via `parameters=[OpenApiParameter(...)]`.

---

## 5. `responses` — success code + every possible error, all with examples

Document the **success code** AND **every error this endpoint can return**. Pull boilerplate errors from `shared/spectacular/responses.py` so we don't rewrite them 50 times.

```python
from shared.spectacular.responses import STANDARD_ERROR_RESPONSES

responses={
    201: OpenApiResponse(
        response=OnboardingSuccessResponseSerializer,
        description="Vendor profile created successfully.",
        examples=[
            OpenApiExample(
                name="Success",
                value={
                    "success": True,
                    "status": 201,
                    "message": "Welcome! Your vendor profile has been created.",
                    "data": {...},
                },
            ),
        ],
    ),
    409: OpenApiResponse(
        description="Vendor already onboarded.",
        examples=[
            OpenApiExample(
                name="Already onboarded",
                value={
                    "success": False,
                    "status": 409,
                    "message": "This account has already been onboarded.",
                },
            ),
        ],
    ),
    **STANDARD_ERROR_RESPONSES["auth"],        # 401
    **STANDARD_ERROR_RESPONSES["validation"],  # 422
    **STANDARD_ERROR_RESPONSES["server"],      # 500
},
```

### What error response examples must show

- The full envelope shape (`success`, `status`, `message`).
- A **human-meaningful** `message` — the actual string the FE will display or compare against.

### Don't document errors that can't happen

- A view with `permission_classes = [AllowAny]` shouldn't document `401`.
- A view that doesn't accept a request body shouldn't document `422`.
- Only document the errors that view can actually emit.

---

## 6. Field-level `help_text` on every serializer field

drf-spectacular pulls `help_text` directly into the schema. Every field on every request serializer must have a one-sentence `help_text`.

```python
business_address = serializers.CharField(
    max_length=500,
    help_text="Full street address. Auto-geocoded to lat/long for delivery radius.",
)
```

- Explain **what the field is**, not just retype its name. `help_text="The business address"` adds nothing.
- For format-constrained fields (phones, dates, enums), include the format in the help text.
- For optional fields, say what happens when omitted.

---

## 7. Permission classes must be explicit

`permission_classes` on the view should match what the description's `**Auth:**` line claims. PR reviewers should verify these line up — a description that says "Admin only" with `permission_classes = [IsAuthenticated]` is a documentation bug.

---

## Checklist for PR reviewers

For every endpoint in a PR, we will check:

- [ ] `summary` is verb-first, ≤ 60 chars.
- [ ] `description` has all 5 required sections (opening / journey / Auth / Prerequisites / Important).
- [ ] `tags` is exactly one tag in `Audience — Resource` format with em dash.
- [ ] `request` includes a serializer AND at least one realistic `OpenApiExample`.
- [ ] `responses` documents the success code AND every error the view can emit, each with an example.
- [ ] Boilerplate errors come from `STANDARD_ERROR_RESPONSES`, not hand-written.
- [ ] Every request serializer field has `help_text`.
- [ ] `permission_classes` matches the `**Auth:**` line.

---

## Reference implementation

The gold-standard references are the admin-side views in
[course_views.py](../api/courses/views/course_views.py) (review queue) and
[platform/views.py](../api/platform/views.py) (platform settings). When in
doubt, mirror their summary/description/tags/responses structure.
