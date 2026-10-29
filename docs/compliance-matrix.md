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
