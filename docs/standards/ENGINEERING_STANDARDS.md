# Engineering Handbook

**Audience:** every backend engineer writing code in these repos — new joiners, contractors, and anyone reviewing a PR.

**Why this exists.** The structure of these codebases is already good. What breaks them is judgement: a fix applied to one call site instead of ten, a test written to pass instead of to catch, a function built but never wired in, a parameter added but never called. Every rule below comes from a real bug found in this codebase, and most of them exist to stop **the frontend finding your bug for you**. Every round trip — FE reports, backend investigates, backend patches, FE retests — is paid for by two teams, and nearly all of them were preventable at the moment the code was written.

**How to use it.** Every rule has a stable ID. In review, cite the ID rather than re-explaining the principle: _"R6.4 — this filter has no test through the URL."_ Appendix B is the one-page version for exactly that.

**Scope.** Written for the Django backends that share the `api/ + config/ + includes/ + shared/` layout. Part 1 is carried in full rather than linked, because most of those repos have no patterns guide of their own. On a non-Django repo, Parts 2–7 and 9–10 still apply as written; Parts 1 and 8 are Django-specific.

**Long-form references**, where the repo has them: `docs/codebase-patterns-guide.md` (full patterns), `docs/standards/swagger_standard.md` (full API documentation standard), `docs/ARCHITECTURE.md`, `docs/CI_CD_GUIDE.md`.

---

## Contents

| Part  |                                                   |
| ----- | ------------------------------------------------- |
| **1** | The system you're joining                         |
| **2** | Investigate before you change anything            |
| **3** | Scope: how far to take a fix                      |
| **4** | Invariants that keep the system stable            |
| **5** | Performance is a correctness concern              |
| **6** | Test the endpoint the frontend will actually call |
| **7** | What makes a test worth having                    |
| **8** | Documenting the endpoint                          |
| **9** | Definition of done                                |
| **10** | Working with reviewers                           |
| **A** | Appendix — bug ledger                             |
| **B** | Appendix — review quick-reference                 |
| **C** | Appendix — done checklist                         |

---

# Part 1 · The system you're joining

## 1.1 Layout

```
config/          Django project config — settings/ split by concern, urls, asgi, celery
api/             One Django app per business domain; api/v1/urls.py aggregates them
shared/          Cross-cutting infrastructure: services, constants, response/, audit/, celery/
includes/        Reusable base classes: mixins/models.py, pagination.py, helpers/
devscripts/      Developer tooling — ci.py is the local CI gate runner
docs/            Documentation
conftest.py      Root pytest fixtures — shared across every app
```

Four principles hold it together: **domain isolation** (one app per domain), **shared infrastructure** (cross-cutting code lives in `shared/` or `includes/`, never copied between apps), **feature completeness** (each app carries its own models, serializers, services, views, tests), and **configuration is code** (modular, versioned, environment-aware settings).

## 1.2 The four layers

```
Views         HTTP only — auth, permissions, calling services, returning responses
Serializers   Validation in, formatting out
Services      Business logic — no HTTP awareness whatsoever
Models        Schema, relationships, indexes, constraints
```

| Layer           | Responsibility                                                              | Must NOT do                                         |
| --------------- | --------------------------------------------------------------------------- | --------------------------------------------------- |
| **Models**      | Schema, relationships, indexes, constraints, `__str__`, small state helpers | Business logic, validation, HTTP                    |
| **Serializers** | Input validation, output formatting, field-level rules                      | Business logic, DB queries, permissions             |
| **Services**    | Business logic, orchestration, external calls, transactions                 | Import `request`/`response`, use HTTP status codes  |
| **Views**       | HTTP handling, auth/permissions, calling services                           | Business logic, complex validation, direct ORM work |

House conventions on top of that:

- **APIView only.** No ViewSets, no generics-with-magic. Handlers are written out explicitly.
- **Services are plain functions**, not classes, with keyword-only arguments: `def suspend_staff_member(*, company, membership_id, actor)`.
- **Serializer files are audience-prefixed** — `company_admin_company_serializer.py`, `staff_serializer.py` — so it's obvious who consumes them.
- **Explicit over clever.** A reader should never have to infer behaviour from framework magic.

## 1.3 Model mixins

Compose models from the shared mixin module — `includes/mixins/models.py` in most repos; grep for `SoftDeleteModelMixin` if yours differs. Never redefine these per app:

- `UUIDPrimaryKeyModelMixin` — UUID PK generated client-side (so `bulk_create` gives you usable ids immediately)
- `DateHistoryModelMixin` — `created_datetime`, `updated_datetime`
- `SoftDeleteModelMixin` — `is_deleted`, `deleted_datetime`; overrides `delete()`
- `UserHistoryModelMixin`, `ApprovalModelMixin` — audit and approval columns

Soft delete is a TUDS requirement, so **every query filters `is_deleted=False`**. Constraints scoped to live rows use `condition=Q(is_deleted=False)`.

## 1.4 The response envelope

Every endpoint returns through `shared/response/`:

```python
custom_success_response(status=200, message="...", data={...})
# → {"success": true, "status": 200, "message": "...", "data": {...}}

custom_error_response(status=404, message="...", data=None, technical_message=None)
# → {"success": false, "status": 404, "message": "...", "technical_message": null}
```

Paginated reads nest under `data`: `data.paginator` (`count`, `page`, `page_size`, `total_pages`, `next`, `next_page_number`, `previous`, `previous_page_number`) and `data.results`. **The frontend parses these key names** — see R6.3.

## 1.5 TUDS — the non-negotiables

| Requirement        | Implementation                            | Verify with                        |
| ------------------ | ----------------------------------------- | ---------------------------------- |
| Zero-trust secrets | `python-decouple` / env — never hardcoded | `grep -r 'SECRET_KEY.*='`          |
| Soft delete        | `SoftDeleteModelMixin`                    | Model inheritance                  |
| Audit trail        | `shared/audit/` on all state changes      | Service code                       |
| Layer separation   | Models → Serializers → Services → Views   | Review                             |
| OpenAPI contract   | `drf-spectacular`, validated in CI        | `manage.py spectacular --validate` |
| Settings split     | `config/settings/` modules                | Directory                          |
| CI enforcement     | Green CI required to merge                | Branch protection                  |

Compliance is tracked in `.tuds-ledger.json` where the repo has one. Intentional deviations belong in `known_deviations` with a reason — not left undocumented.

## 1.6 Adding a feature — the order

1. **Model** + migration (mixins, `Meta.indexes`, constraints)
2. **Serializers** — input and output, `help_text` on every field
3. **Service** — plain functions, typed exceptions (`StaffNotFound`, `ShiftValidationError`), transactions, audit events
4. **View** — APIView, explicit `permission_classes`, map service exceptions to status codes
5. **Docs** — `@extend_schema` dict in `docs/`, per Part 8
6. **URL** — wire it in `urls.py`, then confirm it resolves
7. **Tests** — HTTP-level first (Part 6), then service-level for the branches HTTP can't reach
8. **`python devscripts/ci.py`** — green before you push

---

# Part 2 · Investigate before you change anything

#### R2.1 · Read the code, not the description of the code

**Rule** — Before changing anything, read the actual implementation end to end.
**Why** — Tickets, comments and docstrings describe intent. Bugs live in the gap between intent and implementation, and that gap is invisible if you only read the description.
**Seen here** — `JobDetailView.get` documented itself as _"Retrieve one job by ID for the acting company. Requires `jobs.view` access"_. It was `IsAuthenticated` with a completely unscoped query — any signed-in user could read any company's job. The docstring described the intended behaviour perfectly; the code did something else.
**Check** — You can state what the code does without reading its comments.

#### R2.2 · Verify every claim against source before acting on it

**Rule** — Treat any second-hand statement — a ticket, a teammate's summary, an old comment, your own memory — as a lead to verify, never a fact to build on.
**Why** — Acting on an unverified claim silently inherits someone else's mistake, and you'll be the one who shipped it.
**Seen here** — A prior analysis of this codebase confidently stated that DRF validation errors surface as **422** here. The project actually uses a custom handler (`includes/helpers/exception.py`) that preserves DRF's **400**. Tests written against the claim would have been wrong.
**Check** — For every non-obvious claim in your PR description, you can name the file and line that proves it.

#### R2.3 · Find the root cause — a symptom fix is not a fix

**Rule** — Keep going until you can explain _why_ the bug happened, not just where it shows.
**Why** — Symptom fixes leave the cause in place to resurface somewhere you aren't looking.
**Seen here** — `active_job_for_artisan()` returned wrong jobs. The symptom was a bad filter; the cause was that the filter had been commented out _and_ the commented-out import beside it pointed at `api.jobs.services.artisan_location_service.ACTIVE_JOB_STATUSES` — a different app's enum (`jobs.Job.Status`, not `companies.CompanyJob.Status`). Uncommenting it, the obvious "fix", would still have been wrong.
**Check** — Your PR explains the cause in one sentence, and it isn't "the code was wrong".

#### R2.4 · Look for the existing helper before writing a new one

**Rule** — Search for prior art before adding a function, and follow the established pattern when you find it.
**Why** — Parallel implementations drift apart, and the second one never gets the bug fixes the first one got.
**Seen here** — `_caller_company()` in `customer_service.py` already wrapped the raising resolver safely — but it was private to one file, so nine other call sites each went without. The fix was to promote one shared `company_for_member_or_none()`, not to write the same try/except ten times.
**Check** — You grepped for the behaviour before adding it.

**Part 2 checklist** — □ read the implementation □ verified every claim against source □ can state the root cause □ searched for existing helpers

---

# Part 3 · Scope: how far to take a fix

#### R3.1 · Fix the bug class, not the instance

**Rule** — When you find a bug, grep for every other place with the same shape and fix them together.
**Why** — The instance you were shown is rarely the only one, and the rest are already in production.
**Seen here** — One confirmed 500 from `company_for_member()` being called inline without a try/except. Auditing every call site found **ten**, across inventory, stores, groups, customers and jobs. Nine were latent — shielded only by a permission class that happened to run first, one refactor away from going live.
**Check** — Your PR says how many call sites of this shape exist and that you checked all of them.

#### R3.2 · Don't silently widen — flag instead

**Rule** — Fix what you were asked to fix. When you find unrelated problems, report them; don't quietly bundle them in.
**Why** — A PR that does three unannounced things can't be reviewed properly, and an unrelated change hides in the diff.
**Seen here** — The unscoped `CompanyJob` lookups were found while building live tracking. They were flagged, deliberately left alone, and fixed later in their own pass — so each change got reviewed on its own terms.
**Check** — Everything in your diff is either the task or explicitly called out in the PR.

#### R3.3 · If you're already in it and it's plainly broken, fix it and say so

**Rule** — Broken code inside a function you're already editing gets fixed, and the fix gets its own line in the PR.
**Why** — Leaving a known bug behind in code you just touched means you've reviewed it and endorsed it.
**Seen here** — While adding audit logging to `cancel_exception`, it turned out to set `status = CANCELLED` _before_ testing `if status == APPROVED` — so the branch restoring assignments was permanently dead, and cancelling an approved leave never released anyone. Fixed in place, listed separately in the PR.
**Check** — Every opportunistic fix appears in the PR description as its own item.

**Part 3 checklist** — □ audited the whole bug class □ nothing unrelated snuck in □ opportunistic fixes listed explicitly

---

# Part 4 · Invariants that keep the system stable

#### R4.1 · Tenant scoping goes in the query, never after it

**Rule** — Put `company=` (or the equivalent tenant filter) in the same lookup as the id.
**Why** — A post-fetch check is a second chance to forget. In the query, forgetting is impossible.
**Seen here** — `update_company_job`, `assign_artisan_to_job` and `cancel_job` all did `CompanyJob.objects.get(id=job_id, is_deleted=False)` with no tenant filter, so an admin of **any** company could mutate another company's job by guessing its id.
**Check** — Every single-object lookup in a mutating path names its tenant.

#### R4.2 · A foreign object 404s, it does not 403

**Rule** — An object outside the caller's tenant is reported as _not found_, identically to one that doesn't exist.
**Why** — 403 confirms the id is real, which turns an endpoint into an id-enumeration oracle.
**Seen here** — The scoped lookups added in `_job_scope()` and `get_staff_detail()` collapse both cases into one 404 by construction, because the row simply doesn't match.
**Check** — No code path returns 403 purely because an object belongs to someone else.

#### R4.3 · A permission gate is not a tenant check

**Rule** — Holding a flag proves _what_ a user may do, never _which_ records they may touch. Scope separately, always.
**Why** — Flags are held within a tenant; the object id arrives from the client.
**Seen here** — `HasCompanyFlag(JOBS_ASSIGN)` proves the caller administers _some_ company holding that flag. Every view relying on it for tenancy was cross-tenant-writable. Separately, `IsCompanyAdmin()` checks a global role and resolves no tenant at all — its own docstring says to pair it with `HasCompanyFlag`, and the one view that didn't was the single live-reachable 500.
**Check** — For each endpoint you can name the permission gate _and_, separately, the tenant filter.

#### R4.4 · Never leave commented-out logic

**Rule** — Finish it or delete it. Git remembers.
**Why** — Commented-out code reads as intent and hides that the behaviour is missing. Nothing tests it and nothing lints it.
**Seen here** — A commented-out status filter made `active_job_for_artisan()` match every job ever assigned, and the commented import beside it pointed at the wrong app entirely (R2.3).
**Check** — No commented-out statements in the diff.

#### R4.5 · No magic numbers, and one source per figure

**Rule** — Name your constants, and derive two numbers describing the same thing from one calculation.
**Why** — Two independent derivations of one fact will disagree, usually in production.
**Seen here** — The weekly roster returned `open_slot` per cell meaning _unlimited capacity_ (open only when zero assignees) while `summary.unassigned_slots` computed `roster_shifts * 3 - assignments`, assuming a hardcoded cap of three. Two contradictory capacity models in a single response body.
**Check** — Every literal number is either named or has a comment justifying it.

#### R4.6 · A field does what its name says, or it is removed

**Rule** — An accepted API field must change behaviour. If it doesn't, delete it.
**Why** — A field that is validated and ignored is a documented promise the system breaks silently.
**Seen here** — `confirm_warnings` was declared on the serializer, accepted by the endpoint, and referenced nowhere else — overlapping roster assignments were always created regardless. Callers had every reason to believe they were being protected.
**Check** — Grep each new field name; it appears somewhere other than its own declaration.

#### R4.7 · A function nothing calls is not done

**Rule** — Wiring is part of the feature. Unreferenced code is unfinished work, not a building block.
**Why** — It looks complete in review and does nothing in production.
**Seen here** — `is_artisan_on_active_company_shift()` was fully implemented and correct-ish, and a repo-wide grep found exactly one reference: its own definition. The entire point of the shift feature — that an active shift controls who receives work — had zero runtime effect.
**Check** — Grep every new public function; something other than its definition and its tests calls it.

#### R4.8 · New permission codenames need a backfill migration

**Rule** — Adding a codename to the catalogue requires a data migration granting it to existing tenants.
**Why** — Default role permissions are materialised **once**, at company creation (`_seed_default_roles`). There is no reseed path, so a new codename never reaches any company that already exists.
**Seen here** — `0038_backfill_tracking_permission.py` and `0040_backfill_shifts_permission.py`. Both are idempotent, both have working reverses, both are tested.
**Check** — Adding a codename? There's a migration in the same PR.

#### R4.9 · Behaviour-changing gates ship opt-in

**Rule** — A new rule that can reject existing traffic must be scoped so current tenants are unaffected until they adopt it.
**Why** — A gate enabled globally at merge time is an outage, not a feature.
**Seen here** — Every company had zero `CompanyShift` rows, so enforcing shift eligibility unconditionally would have failed **100%** of job assignments and starts everywhere on deploy. `_guard_shift_eligibility()` exempts companies with no shift definitions; the gate switches on per-company at first use.
**Check** — You can state what happens to a tenant that has never used this feature.

**Part 4 checklist** — □ tenant in the query □ foreign → 404 □ permission ≠ tenancy □ no commented-out code □ no magic numbers □ every field does something □ everything new is wired in □ backfill migration if codenames changed □ new gates are opt-in

---

# Part 5 · Performance is a correctness concern

> A correct endpoint that issues a query per row is a broken endpoint at scale. The cost lands in three places: latency the user feels, database capacity you pay for, and an incident at 3am when a list that was fine with 20 rows meets 20,000. Decide the shape of the cost while you're writing the query — it is far cheaper than discovering it from a bill or an outage.

#### R5.1 · Know the query count, and keep it independent of result size

**Rule** — Before shipping a read or a batch write, know how many queries it issues, and confirm that number doesn't grow with the number of rows.
**Why** — O(n) queries is the most common way a feature that passed review takes down a database. It always looks fine on a dev machine holding ten rows.
**Seen here** — `assign_multiple_artisans` ran a full SELECT-then-INSERT cycle per artisan, each in its own transaction. Assigning 20 artisans meant 40+ round trips — and if row 19 failed, rows 1–18 were already committed.
**Check** — You can state it in the PR: *"fixed at 5 queries regardless of batch size."*

#### R5.2 · Batch writes — never one INSERT per row

**Rule** — Validate the whole batch first, then write it with `bulk_create` / `bulk_update` in a single statement.
**Why** — N inserts cost N round trips and N transactions; one bulk write costs one of each. Validating up front also eliminates partial-commit bugs for free.
**Seen here** — `assign_multiple_artisans` and `copy_week` both looped `.create()`. Rewritten as validate-then-`bulk_create`, `copy_week` writes shifts and their assignments in two statements instead of N+M.
**Check** — Two preconditions before `bulk_create`: PKs are client-generated (the UUID mixin), so ids are usable immediately for dependent rows; and no `pre_save`/`post_save` signal is needed, because `bulk_create` skips them. `api/companies/` has none — grep before assuming the same elsewhere.

#### R5.3 · Never query inside a loop

**Rule** — Fetch once outside the loop, group in Python, read from the dict inside it.
**Why** — This is the N+1, and it hides well: the queryset looks already-fetched, but calling `.filter()` on it re-queries every time.
**Seen here** — `copy_week` held `source_assignments` as a queryset, then called `source_assignments.filter(roster_shift=source_rs)` once per source shift — an extra query per shift, invisible in review. Now grouped once into a dict keyed by `roster_shift_id`.
**Check** — No `.filter()`, `.get()`, `.count()` or `.exists()` inside a `for` body.

#### R5.4 · Declare `select_related` / `prefetch_related` on every list read

**Rule** — Forward FKs → `select_related`; reverse FKs and M2M → `prefetch_related`, with `to_attr` when a serializer reads it.
**Why** — Serializers touch related objects once per row, so a 50-row page silently becomes 50+ queries.
**Seen here** — `list_staff` declares `select_related("user", "user__profile")` plus `Prefetch(..., to_attr="active_role_assignments")`. And the trap worth knowing: `StaffListSerializer.get_roles` reads that attribute when it's present but **falls back to a per-object query when it isn't** — so serialising an unprefetched queryset still returns correct data, at one query per row, with nothing failing to warn you.
**Check** — For every serializer field that crosses a relation, the queryset feeding it declares that relation.

#### R5.5 · Build lookup dicts for grid reads

**Rule** — For matrix or calendar-shaped payloads, fetch each dataset once and index it by the key you'll look it up by.
**Why** — A grid is a nested loop; a query inside it costs rows × columns queries.
**Seen here** — `get_weekly_roster` fetches shifts, occurrences and assignments once each, then builds `assignments_by_shift_date[(roster_shift_id, date)]` and `shifts_by_date[date]`. The 7-day × N-shift render loop issues zero queries. Copy this pattern.
**Check** — The render loop reads only from structures already in memory.

#### R5.6 · Push the work into the database

**Rule** — Use aggregates, annotations and `exists()` rather than pulling rows into Python to count or test them.
**Why** — The database does it faster, over less data, without shipping rows across the wire.
**Seen here** — `staff_dashboard_metrics` counts with filtered `.count()`; artisan stats use `Count(..., filter=Q(...))` for conditional counts. Counter-example in the same codebase: `get_weekly_roster` calls `.count()` on querysets it has *already* iterated — two extra round trips for numbers it could derive in Python.
**Check** — `.exists()` for a boolean, never `len(qs)` or `qs.count() > 0`; and never re-`.count()` a queryset you already materialised.

#### R5.7 · Index what you filter, join and sort on

**Rule** — Add `Meta.indexes` matching the query shapes the feature actually issues, composites included.
**Why** — An unindexed filter is a sequential scan that degrades as the table grows — the classic "fine for months, then suddenly not".
**Seen here** — The shift models carry composites matching their documented access patterns: `(company, roster_date, is_deleted)`, `(company, starts_at, ends_at)`. By contrast the eligibility lookup filters `CompanyMembership` on company + user + status + is_deleted + is_customer, which no single existing index fully covers.
**Check** — For each new query shape, you can name the index that serves it.

#### R5.8 · Prove it with a query-count test

**Rule** — Lock the guarantee into a test that compares across input sizes rather than asserting a fixed number.
**Why** — Query counts regress silently; nothing else in CI notices an N+1 coming back.
**Seen here** — The bulk-assign test runs a batch of 1 and a batch of 5 inside `CaptureQueriesContext` and asserts the two counts are **equal**. A hardcoded number would have been a snapshot of today's implementation (R7.4); equality is the property that actually matters.
**Check** — Batch and list endpoints have a test that fails if a query moves back inside a loop.

**Part 5 checklist** — □ query count known and independent of row count □ writes batched □ no queries inside loops □ `select_related`/`prefetch_related` declared □ grid reads use lookup dicts □ counting done in the DB □ indexes match query shapes □ query-count test present

---

# Part 6 · Test the endpoint the frontend will actually call

> Service tests prove your logic. They prove **nothing** about the thing the frontend consumes — URL wiring, permission classes, status codes, response shape. Anything tested only below HTTP is a bug the FE finds for you, and each one costs a report, an investigation, a patch and a retest across two teams.

#### R6.1 · Every endpoint gets an HTTP test, not just its service

**Rule** — If it has a URL, a test calls it over HTTP with a real authenticated client.
**Why** — Service tests bypass routing, permissions and serialisation — exactly where integration breaks.
**Seen here** — The whole `/roles/*` surface (create, list, detail, member-counts, permission catalogue) was fully built and fully service-tested with **zero** HTTP tests. Both permission bugs living in it — `MEMBERS_INVITE` granting role creation, `MEMBERS_VIEW` granting role edit and delete — were invisible to those tests by construction, because permission classes never ran.
**Check** — Every view class in the diff appears in a test that issues a real request.

#### R6.2 · Call the literal URL, not `reverse()`

**Rule** — Hardcode the path string the frontend uses: `"/api/v1/companies/staff/"`.
**Why** — `reverse()` follows a renamed route and keeps passing; the FE's hardcoded client does not. A renamed path _should_ break your test, because it breaks them.
**Seen here** — House convention across `test_tracking.py`, `test_staff_management.py`, `test_shifts.py`.
**Check** — No `reverse()` in new endpoint tests.

#### R6.3 · Assert the envelope and the payload shape, not just the status code

**Rule** — Check the keys the FE reads: `success`, `status`, `message`, `data`, and inside `data` the actual field names — including `paginator` / `results` on paginated reads.
**Why** — A 200 carrying a renamed field is still a broken integration, and a status-code-only assertion sails straight past it.
**Seen here** — `data.paginator` and `data.results` are the documented contract in `staff_docs.py`; the FE parses those names directly.
**Check** — Your success test asserts on real field names, not only `status_code`.

#### R6.4 · Exercise every query param and filter through the URL

**Rule** — Each documented param gets a test that actually passes it.
**Why** — Params are contract surface. An untested one is a 500 waiting for whoever reads the docs and uses it.
**Seen here** — `?search=` on the staff list raised `FieldError` (a **500**) on every call, because it filtered `user__first_name` — a field `User` doesn't have. `?group_id=` on the weekly roster raised `FieldError` too, filtering `CompanyShift` through a relation it doesn't have. Both were live in the API contract and in Swagger. Neither had ever been called by a test.
**Check** — Every param in your `@extend_schema` appears in a test.

#### R6.5 · Cover every status the frontend branches on

**Rule** — Test 200/201, 400, 401, 403, 404 and 409 wherever they're reachable.
**Why** — The FE writes a branch per status. Untested branches are untested on both sides.
**Seen here** — The staff and roster suites assert the full ladder: validation 400, unauthenticated 401, wrong-flag 403, cross-tenant 404, conflict 409.
**Check** — Every status documented in Swagger has a test reaching it — and if none can, it shouldn't be documented (R7 checklist).

#### R6.6 · Test each role that can call it — including the refused ones

**Rule** — Assert the allowed roles succeed _and_ the disallowed ones are refused.
**Why** — "Permission denied" is part of the contract. Untested, a gate silently opens during a refactor.
**Seen here** — The role-CRUD regression tests build a custom role holding `company.full` plus exactly one narrow flag, then assert GET succeeds and PATCH/DELETE return 403 — proving the split actually gates rather than assuming it.
**Check** — At least one test per endpoint asserts a 403 for an insufficient role.

#### R6.7 · When the contract changes, the test changes — tell the FE then

**Rule** — A test you had to edit to accommodate new behaviour is a client-visible change. Say so in the PR before deploy.
**Why** — The FE cannot plan for a change they learn about from a production error.
**Seen here** — Two in one pass: job detail moved 200 → **404** for non-members, and roster assignment moved 201 → **409** on an unconfirmed overlap. Both were flagged in the PR under "what the reviewer should know".
**Check** — Every modified test assertion is either a bug fix or a line in the PR's behaviour-change list.

**Part 6 checklist** — □ every endpoint tested over HTTP □ literal URLs □ envelope and field names asserted □ every param exercised □ every status covered □ refusals tested □ behaviour changes flagged to FE

---

# Part 7 · What makes a test worth having

#### R7.1 · A test that cannot fail is worse than none

**Rule** — Before committing a test, make sure there's a realistic change that would break it.
**Why** — A test that always passes gives coverage numbers and false confidence, and stops anyone writing the real one.
**Seen here** — `test_tenant_isolation` created its "other company" member on the **same** company and asserted `resp.status_code in (201, 400)` — true for essentially any response. Its own comment admitted it: _"just ensure no crash"_. It passed for months while the endpoint was never tested for tenant isolation at all.
**Check** — Break the code deliberately; confirm the test goes red.

#### R7.2 · Test the path you changed

**Rule** — New parameter, new branch, new guard — each gets its own test.
**Why** — Untested new code is the most likely code in the system to be wrong.
**Seen here** — `cancel_exception`'s restore branch was dead from the day it was written because no test ever cancelled an _approved_ exception.
**Check** — Every branch you added is entered by some test.

#### R7.3 · Prove the negative

**Rule** — Assert that the wrong caller is refused, not just that the right one succeeds.
**Why** — Happy-path-only suites pass equally well when authorisation is missing entirely.
**Seen here** — `TestJobCrossTenantIsolation` asserts a rival company's job can't be patched, cancelled, assigned or read — and, crucially, that the same-company path still works, so the fix didn't just break everything equally.
**Check** — Each security-relevant test has a positive and a negative case.

#### R7.4 · Assert properties, not snapshots

**Rule** — Assert the invariant you care about, not a number that happens to be true today.
**Why** — A snapshot breaks on every unrelated refactor and teaches people to re-record it without thinking.
**Seen here** — A bulk-assign test first hardcoded `django_assert_num_queries(6)` — an arbitrary guess that failed at 11. The property that actually matters is _cost doesn't scale with batch size_, so the test now runs a batch of 1 and a batch of 5 and asserts the counts are **equal**. That survives refactors and still catches a reintroduced N+1.
**Check** — Ask what a failure would mean. "The number changed" is the wrong answer.

#### R7.5 · Reuse the shared fixtures; duplicate small helpers locally

**Rule** — Take users, clients and roles from the root `conftest.py`; keep small per-file helpers local rather than importing across test modules.
**Why** — Shared fixtures keep setup consistent; local helpers keep test files independently readable and refactorable.
**Seen here** — `make_user`, `company_admin_client`, `approved_company`, `subcategory` come from root `conftest.py`; `_create_job` / `_auth_client` are duplicated per file by design.
**Check** — No test module imports helpers from another test module.

#### R7.6 · Run them before you say it's done

**Rule** — Written is not passing. Run the suite.
**Why** — Code that has never executed is a guess.
**Seen here** — A missing `CompanyShiftSwap` import made 6 endpoints raise `NameError` and 3 tests fail; nobody had run them. Behind that failure hid a second bug: `test_full_swap_lifecycle` skipped the `accept` step entirely and contradicted its own sibling test — it had never once executed successfully.
**Check** — You have the run output, and you read it.

**Part 7 checklist** — □ each test can fail □ new branches covered □ negatives asserted □ properties not snapshots □ shared fixtures reused □ suite actually run

---

# Part 8 · Documenting the endpoint

Full rules: `docs/standards/swagger_standard.md` where the repo has it. The target: **a frontend developer opens the schema, finds the endpoint, and knows what to send, what comes back, who may call it, and what every error means — without asking you.**

Every endpoint needs:

- **`summary`** — imperative, short. _"Suspend a staff member"_.
- **`description`** — with all three blocks, every time:
  - `**Auth:**` the role and any extra gate — always stated, even when public
  - `**Prerequisites:**` what must already be true server-side — or `None`
  - `**Important:**` gotchas, side effects, irreversibility, rate limits — or `None`
- **`tags`** — exactly one, formatted `Audience — Resource`.
- **`request`** — the serializer, plus at least one `OpenApiExample`.
- **`responses`** — the success code **and every error it can return**, each with an example. Don't document errors that can't happen.
- **`help_text`** on every serializer field.
- **explicit `permission_classes`** on the view.

For a body-less POST, set `"request": None` — otherwise the schema generator reports an error it can't resolve.

#### R8.1 · A docstring must not lie

**Rule** — If the docstring and the code disagree, that's a bug in the code until proven otherwise — fix one of them in the same PR.
**Why** — Docstrings are read as the specification. A false one actively misleads the next reader into trusting a guarantee that isn't enforced.
**Seen here** — `JobDetailView.get`: _"Retrieve one job by ID for the acting company. Requires `jobs.view` access."_ In reality `IsAuthenticated` with an unscoped query — any authenticated user could read any company's job, including customer name, address, phone and invoice. The docstring had described the correct behaviour all along; only the code was missing.
**Check** — Read each docstring you touched against the code beneath it.

**Part 8 checklist** — □ summary □ Auth/Prerequisites/Important □ one tag □ request example □ every error documented with an example □ field `help_text` □ explicit permissions □ docstrings true

---

# Part 9 · Definition of done

## 9.1 Local CI green

```bash
python devscripts/ci.py      # or: python ci.py — the runner sits at one or the other
```

The runner is the convenience; **the gates are the contract.** Where a repo has no runner yet, run them by hand — in that order, and add the runner while you're there:

| Gate                                                           | Command                                             | Blocking      |
| -------------------------------------------------------------- | --------------------------------------------------- | ------------- |
| Lint                                                           | `ruff check .`                                      | **yes**       |
| Format                                                         | `ruff format --check .`                             | informational |
| Migration drift                                                | `python manage.py makemigrations --check --dry-run` | **yes**       |
| OpenAPI contract                                               | `python manage.py spectacular --validate`           | **yes**       |
| Tests                                                          | `pytest -q --reuse-db`                              | **yes**       |

It mirrors the remote pipeline, so **never push red**. If tests can't run on your host, run them in Docker — `docker compose exec -T web python -m pytest -q`.

## 9.2 Report faithfully

- If tests fail, **say so and paste the output**. Don't summarise a failure as a pass.
- **Separate pre-existing failures from yours.** State the count you started with and the count you ended with.
- **Say what you skipped** and why. An unstated gap is discovered by someone else, later, at higher cost.
- Don't claim done for work that's merely written. Done means verified.

## 9.3 Before opening the PR

Migration added if models or codenames changed · behaviour changes listed · new endpoints documented and HTTP-tested · nothing commented-out · no debug prints · secrets in env, never in the diff.

---

# Part 10 · Working with reviewers

**Surface real decisions; don't silently pick.** When a choice has product consequences — an enforcement default, a capacity model, a breaking change — put it to the reviewer _with a recommendation_. A menu of options with no opinion pushes your work onto them; a recommendation with reasoning lets them agree in one line.

**Distinguish decisions from implementation details.** Framework choices and naming don't need sign-off. Anything a user or the FE can notice does.

**Call out client-visible changes explicitly**, in their own section. Status code changes, new required parameters, changed response shapes.

**Use the three-section PR shape:**

1. **What has changed?** — one bolded lead sentence per change, then the explanation and root cause.
2. **Where were the changes done?** — grouped by area, marked `(new)` / `(updated)`, `path — what it does`.
3. **What should the reviewer know?** — behaviour changes, risks, decisions taken, test results.

---

# Appendix A · Bug ledger

Every rule here is derived from a real defect in this codebase. The structural layer was correct in all of them — the judgement layer wasn't.

| Bug                                                                                                                                  | Rule       |
| ------------------------------------------------------------------------------------------------------------------------------------ | ---------- |
| `active_job_for_artisan` matched any job ever assigned — filter commented out, commented import pointed at another app's enum        | R2.3, R4.4 |
| Dead Redis publish on every heartbeat — zero subscribers, forever                                                                    | R2.1, R2.4 |
| `list_staff` `?search=` 500'd; weekly roster crashed on assigned shifts (both used `user.first_name`, which doesn't exist on `User`) | R6.4, R7.2 |
| Whole `/roles/*` surface built and service-tested with zero HTTP tests; both permission bugs in it invisible to those tests          | R6.1       |
| `test_tenant_isolation` asserted `status in (201, 400)` against a same-company member                                                | R7.1       |
| `CompanyShiftSwap` missing from an import — 6 endpoints raised `NameError`, 3 tests had never run                                    | R7.6       |
| `is_artisan_on_active_company_shift` fully written, referenced nowhere                                                               | R4.7       |
| 10 unguarded `company_for_member()` call sites; one live-reachable 500                                                               | R3.1       |
| Unscoped `CompanyJob.objects.get(id=...)` on 3 mutators, plus an unscoped detail read open to any authenticated user                 | R4.1, R4.3 |
| `cancel_exception` set status before testing it — restore branch permanently dead                                                    | R3.3, R7.2 |
| `confirm_warnings` accepted and ignored                                                                                              | R4.6       |
| `unassigned_slots` hardcoded `* 3` while `open_slot` assumed unlimited capacity                                                      | R4.5       |
| `JobDetailView.get` docstring promised company scoping the code never did                                                            | R8.1       |
| `assign_multiple_artisans` and `copy_week` wrote one INSERT per row, each in its own transaction | R5.1, R5.2 |
| `copy_week` re-filtered an already-fetched queryset once per source shift — a textbook N+1 | R5.3 |
| Shift eligibility would have blocked 100% of job assignments on deploy                                                               | R4.9       |

# Appendix B · Review quick-reference

Cite the ID in PR comments.

|          |                                                  |
| -------- | ------------------------------------------------ |
| **R2.1** | Read the code, not the description of it         |
| **R2.2** | Verify every claim against source                |
| **R2.3** | Find the root cause                              |
| **R2.4** | Look for the existing helper first               |
| **R3.1** | Fix the bug class, not the instance              |
| **R3.2** | Don't silently widen — flag instead              |
| **R3.3** | Already in it and plainly broken? Fix it, say so |
| **R4.1** | Tenant scoping goes in the query                 |
| **R4.2** | Foreign object 404s, not 403s                    |
| **R4.3** | A permission gate is not a tenant check          |
| **R4.4** | Never leave commented-out logic                  |
| **R4.5** | No magic numbers; one source per figure          |
| **R4.6** | A field does what its name says, or goes         |
| **R4.7** | A function nothing calls is not done             |
| **R4.8** | New codenames need a backfill migration          |
| **R4.9** | Behaviour-changing gates ship opt-in             |
| **R5.1** | Know the query count; keep it independent of row count |
| **R5.2** | Batch writes — never one INSERT per row |
| **R5.3** | Never query inside a loop |
| **R5.4** | Declare `select_related` / `prefetch_related` |
| **R5.5** | Build lookup dicts for grid reads |
| **R5.6** | Push counting and filtering into the database |
| **R5.7** | Index what you filter, join and sort on |
| **R5.8** | Prove it with a query-count test |
| **R6.1** | Every endpoint gets an HTTP test                 |
| **R6.2** | Literal URL, not `reverse()`                     |
| **R6.3** | Assert the envelope and field names              |
| **R6.4** | Exercise every param through the URL             |
| **R6.5** | Cover every status the FE branches on            |
| **R6.6** | Test refusals, not just successes                |
| **R6.7** | Contract changed? Tell the FE before deploy      |
| **R7.1** | A test that cannot fail is worse than none       |
| **R7.2** | Test the path you changed                        |
| **R7.3** | Prove the negative                               |
| **R7.4** | Assert properties, not snapshots                 |
| **R7.5** | Shared fixtures, local helpers                   |
| **R7.6** | Run them before saying it's done                 |
| **R8.1** | A docstring must not lie                         |

# Appendix C · Done checklist

Copy into the PR description.

```
Investigation
□ Read the implementation, not just the description  (R2.1)
□ Every claim verified against source                (R2.2)
□ Root cause identified and stated                   (R2.3)

Scope
□ Whole bug class audited, not one instance          (R3.1)
□ Nothing unrelated bundled in silently              (R3.2)

Correctness
□ Tenant scoping inside the query                    (R4.1)
□ Permission gate and tenant check are separate      (R4.3)
□ No commented-out code, no magic numbers            (R4.4, R4.5)
□ Everything new is actually wired in                (R4.7)
□ Backfill migration if codenames changed            (R4.8)
□ New gates are opt-in for existing tenants          (R4.9)

Performance
□ Query count known, independent of result size      (R5.1)
□ Writes batched, no INSERT-per-row                  (R5.2)
□ No queries inside loops                            (R5.3)
□ select_related / prefetch_related declared         (R5.4)
□ Indexes match the new query shapes                 (R5.7)
□ Query-count test on batch/list endpoints           (R5.8)

Tests
□ HTTP test per endpoint, literal URLs               (R6.1, R6.2)
□ Envelope and field names asserted                  (R6.3)
□ Every param and status covered                     (R6.4, R6.5)
□ Refusals tested                                    (R6.6)
□ Each test can actually fail                        (R7.1)
□ Suite run, output read                             (R7.6)

Docs
□ Auth / Prerequisites / Important all present
□ Every error documented with an example
□ Docstrings match the code                          (R8.1)

Done
□ python devscripts/ci.py green
□ Behaviour changes listed for the FE                (R6.7)
□ Pre-existing failures separated from mine
```
