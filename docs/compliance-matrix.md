# Course Builder receiver compliance evidence

Scope: local Studio receiver hardening, 2026-10-29. Revision: working tree on
`feat/studio-api-handoff`, base `b66403d102fbeaed4c29e87b93ccde7de7e107db`; no committed release revision. This is not a product-wide certification.

| Pillar/control | Source | Environment / implementation | Verification / evidence | Deployed / operational | Gap / dependency / owner |
| --- | --- | --- | --- | --- | --- |
| P1 discoverability | MIE view schema annotations | Local API schema | Migration consistency checked; live schema unverified | Unverified | Full discovery and deployed contract; backend owner |
| P2 MCP | Existing platform | Not assessed in receiver scope | No operational evidence | Unverified | MCP authentication, scope and audit assessment; platform owner |
| P3 mobile/offline | Lesson API transcript field | Local API only | Frontend playback not exercised | Unverified | Mobile captions/playback and offline flows; frontend owner |
| P4 isolation | MIE developer ownership filters | Local receiver implemented | Cross-developer receipt denial test | Unverified | Database-level tenant isolation assessment; platform owner |
| P5 boundaries/events | Studio services and submission/course domain services | Local staged workflow | Approval identities, text/video and replay regression tests | Unverified | Real webhook-to-delivery journey and app entitlements; integration owner |
| P6 reliability | Migrations 0012–0014, courses 0022, receipts, upload descriptors | Local implementation | PostgreSQL regression, stored-byte integrity and migration consistency checks | Unverified | Storage latency, concurrent replay, backup/restore and CI exact revision; backend/operations |
| P7 security | MIE authentication, bounded serializers, SHA-256 verification | Local implementation | Missing credential, wrong developer, malformed input and checksum tests | Unverified | Immutable media/versioning, deployed storage and broader privacy/security controls; security owner |

See `studio-receiver-handoff.md` for deployment dependencies and explicit operational limitations.

Local verification: 81 tests and 5 subtests passed using `/private/tmp/scb-platform-tests.py`
on the disposable local PostgreSQL test database. Selection: `test_studio_review_api`,
`test_approval_course_identity`, `test_course_push_api`, `test_staged_course_video_api`,
`shared/uploads/test_storage_service.py`. Ruff and `git diff --check` passed;
`manage makemigrations --check --dry-run` reported no changes. Existing Django deprecation
and missing staticfiles warnings remain. Paired Studio: 30 unit and 13 integration tests
passed on `scb_receiver_integrity_test`, with `studio_app` NOSUPERUSER NOBYPASSRLS.


## Staging reconciliation verification

2026-10-29: receiver merged with staging `88adcdb` in local revision `a96723c`,
followed by the test fixture correction. Fresh-database run:
`python -m pytest api/mie/tests shared/uploads -q --disable-warnings --create-db`
passed **386 tests and 227 subtests**. This covers staging's idempotency,
video-assignment/archive and reserved-course-ID behavior as well as the receiver.
The initial reused database contained migration-test fixtures; the clean recreation
removed that contamination. Full Ruff and migration consistency checks pass.
MIE migration chain is now staging 0012/0013 → receiver 0014 → integrity 0015;
courses 0022 adds the transcript. Earlier local 0012 identity migration was removed.
Paired Studio supports separate explicit assignment and archive cancellation;
30 unit and 13 PostgreSQL integration tests pass with external boundaries mocked.
No real storage/playback or deployment evidence is asserted.


## CI permission inventory correction

CI run `40945526414` passed 2,269 tests and 465 subtests but failed the handler
coverage inventory: the two Studio endpoints were missing from the explicit MIE
(non-staff authentication) classification. The inventory now documents their MIE
developer authentication and submission ownership boundary, matching existing MIE
push endpoints. Negative upload tests also check missing credentials (401) and a
different approved developer (404). This does not remove or weaken any endpoint gate.
The follow-up CI run must pass before promotion; no deployment is authorized here.
