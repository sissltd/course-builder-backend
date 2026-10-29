# Staging → main conflict resolution (PR #103)

Parents: staging `8b85178ab66fc88c60a6475613a9bd0397fc440f` and main
`fe0156f`. Their merge base is `636ae512941b093eff159496d395ecd78323b64e`.
The merge records main as an ancestor without replacing staging's later application code.
The application/configuration/migration tree remains identical to the tested staging parent.
The only non-documentation content addition is the main-side local automation ignore.

## Resolution decisions

- Authentication, sessions, MFA, user preferences and activities: retain staging's later
  permission-backed implementation, identity links, token request scoping, and serializers.
  The main-side MFA implementation is superseded by staging PR #83 / `5dc6173`
  (admin MFA opt-in), and challenge behavior by PR #81 / `4116933`.
  This preserves the deliberate product behavior; mandatory-admin-MFA compliance
  remains an open P7 gap rather than being claimed as satisfied by this merge.
- Catalog, course appeals/reviews, wallet, notification and platform settings: retain
  staging's later domain services, permission gates, schema and tests. Do not resurrect
  deleted `api/users/permissions.py` or `api/courses/tests/test_review_api.py`; those
  interfaces were replaced by the authorization/catalog/reviews modules.
- CI/runtime configuration and dependencies: retain staging's current storage variable
  names, pinned dependencies, pytest contract checks, FFmpeg installation and existing
  staging deployment workflow. Do not downgrade the runner to the old test command.
- Remove automatic-merge duplicates: `AppealStatus`, notification preference fields,
  and the repeated field-encryption example were already present on staging.
- Do not reintroduce main's `courses/0010_courseappeal.py`: it depends on removed
  `0009_course_duration_estimate_minutes_course_version_and_more` and recreates
  `CourseAppeal`, already created by staging's rebuilt `0001_initial`.
  No existing staging migration is rewritten by this merge.
- Preserve both branches' local automation ignore patterns.

## Verification and release boundary

Full Ruff and `git diff --check` pass. Migration consistency reports no changes.
Fresh-database regression: **322 tests and 45 subtests passed** in 96.31 seconds.
Selection: `api/authentication/tests`, `api/mie/tests/test_studio_review_api.py`,
`api/authorization/tests/test_gate_coverage.py`, course appeal API/service tests,
notification tests and platform settings tests. Existing warnings remain.
Tests ran on the separate local `test_scb_merge_resolution` PostgreSQL database.

The merge resolves Git history, not an upgrade of a live main database. Main's older
migration history differs from staging's rebuilt schema; production upgrade and data
migration require a separate backup/restore rehearsal before deployment. No production
merge or deployment is performed by this conflict-resolution task. Existing staging CD
may run after pushing the resolved staging head; that is distinct from production release.
